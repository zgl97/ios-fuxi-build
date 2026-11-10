#!/usr/bin/env python3
"""Stage Metal into an already mounted writable root. No mounting or phone access."""
import argparse
import os
from pathlib import Path
import plistlib
import re
import shutil
import struct
import stat


def macho(path,kind):
    raw=path.read_bytes()
    if len(raw)<32 or struct.unpack_from('<4I',raw)!=(0xfeedfacf,0x100000c,0,kind):
        raise ValueError('expected plain arm64 Mach-O')
    ncmds,total=struct.unpack_from('<2I',raw,16);pos=32;signed=False;minimum=False
    if total>len(raw)-32:raise ValueError('truncated commands')
    for _ in range(ncmds):
        if pos+8>32+total:raise ValueError('truncated command')
        cmd,size=struct.unpack_from('<2I',raw,pos)
        if size<8 or pos+size>32+total:raise ValueError('invalid command')
        if cmd==0x1d:
            if size<16:raise ValueError('invalid signature command')
            off,length=struct.unpack_from('<2I',raw,pos+8)
            signed=length>0 and off+length<=len(raw)
        if cmd==0x32:minimum=struct.unpack_from('<2I',raw,pos+8)==(2,0x100000)
        pos+=size
    if pos!=32+total or not signed or not minimum:raise ValueError('requires signed iOS 16.0 artifact')


def validate_clients(path,enable_backboardd=False):
    raw=path.read_bytes()
    if len(raw)>8192 or b'\0' in raw:raise ValueError('invalid client config')
    seen={}
    for line in raw.split(b'\n'):
        line=line.strip(b' \t\r')
        if not line or line.startswith(b'#'):continue
        mode='allow'
        if line.startswith(b'census '):mode='census';line=line[7:].lstrip(b' \t')
        if not re.fullmatch(rb'[A-Za-z0-9_.-]{1,63}',line):raise ValueError('invalid client name')
        # ClientGate.hpp hiddenClient(): both abort on any visible device (docs-lanes/cooling.md).
        if line in (b'SpringBoard',b'mediaserverd'):raise ValueError(line.decode()+' is permanently denied')
        if line==b'backboardd' and (not enable_backboardd or mode!='allow'):
            raise ValueError('backboardd requires --enable-backboardd and an allow entry')
        if line in seen and seen[line]!=mode:raise ValueError('conflicting client modes')
        seen[line]=mode
    # The flag generates the protected config key; shipped input stays smoke-only.
    if enable_backboardd and b'backboardd' not in seen:raw=raw.rstrip(b'\n')+b'\nbackboardd\n'
    if len(raw)>8192:raise ValueError('enabled client config exceeds 8192 bytes')
    return raw


def backboard_account(root):
    """Resolve target launchd metadata, never the staging host's account database."""
    root=Path(root).resolve()  # the staging root may itself sit below a host symlink
    path=root/'System/Library/LaunchDaemons/com.apple.backboardd.plist'
    if path.is_symlink() or not path.resolve().is_relative_to(root):raise ValueError('linked backboardd job')
    job=plistlib.loads(path.read_bytes())
    user=job.get('UserName')
    if user is None:return 0,0  # launchd system job default; runtime still checks geteuid().
    if not isinstance(user,str) or not re.fullmatch(r'[A-Za-z0-9_-]+',user):raise ValueError('invalid backboardd UserName')
    for rel in ('private/etc/master.passwd','private/etc/passwd','etc/master.passwd','etc/passwd'):
        path=root/rel
        if not path.resolve().is_relative_to(root) or not path.is_file():continue
        for line in path.read_text().splitlines():
            fields=line.split(':')
            if len(fields)>=4 and fields[0]==user and fields[2].isdigit() and fields[3].isdigit():
                uid,gid=int(fields[2]),int(fields[3])
                if uid<2**32-1 and gid<2**32-1:return uid,gid
    raise ValueError('backboardd account absent from target passwd database')


def prepare_backboard_storage(root,account):
    root=Path(root).resolve()
    uid,gid=account;parent=root/'private/var/db/fuxi-metal';client=parent/('backboardd-%u'%uid)
    # Validate before mutation. A root-only M9i directory can be made traversable;
    # caches/strike files stay inside private client directories, never writable by others.
    for path,owner,modes in ((parent,0,(0o700,0o755)),(client,uid,(0o700,))):
        if not path.resolve().is_relative_to(root) or any(p.is_symlink() for p in [path,*path.parents] if p!=root and root in p.parents):
            raise ValueError('linked storage path')
        if path.exists():
            s=path.stat()
            if not stat.S_ISDIR(s.st_mode) or s.st_uid!=owner or stat.S_IMODE(s.st_mode) not in modes:
                raise ValueError('unsafe backboardd storage ownership/mode')
    missing=[];path=parent
    while not path.exists():missing.append(path);path=path.parent
    for path in reversed(missing):path.mkdir();os.chown(path,0,0);os.chmod(path,0o755)
    os.chmod(parent,0o755)
    if not client.exists():client.mkdir(mode=0o700);os.chown(client,uid,gid);os.chmod(client,0o700)
    print('staged /private/var/db/fuxi-metal/backboardd-%u uid=%u gid=%u 0700'%(uid,uid,gid))


def stage(root,icd,bundle,smoke,conf,clients=Path(__file__).with_name('metal-clients.conf'),translator=None,metallib=None,source=None,ca_smoke=None,ca_conf=Path(__file__).with_name('ca-smoke.conf'),enable_backboardd=False,native_bundle=None,native_bridge=None,native_source=None,prepare_pipeline_cache=False):
    if os.geteuid()!=0:raise PermissionError('run as root to set root:wheel 0:0')
    root=root.resolve()
    if not (root/'System/Library').is_dir():raise ValueError('expected mounted iOS root')
    if not re.fullmatch(rb'[ \t\r\n]*[0-9]{1,2}[ \t\r\n]*',conf.read_bytes()) or conf.stat().st_size>31:
        raise ValueError('config must contain max stage 0..10')
    if not re.fullmatch(rb'[ \t\r\n]*[0-5][ \t\r\n]*',ca_conf.read_bytes()) or ca_conf.stat().st_size>31:
        raise ValueError('CA config must contain max stage 0..5')
    if ca_smoke is None:ca_smoke=smoke.parent/'fuxi-ca-smoke'
    if translator is None:translator=smoke.parent/'libmetal2vulkan.dylib'
    if metallib is None:metallib=smoke.parent/'metal-smoke.metallib'
    if source is None:source=smoke.parent/'metal2vulkan-source.tar.gz'
    client_data=validate_clients(clients,enable_backboardd)
    account=backboard_account(root) if enable_backboardd or prepare_pipeline_cache else None
    info=plistlib.loads((bundle/'Info.plist').read_bytes())
    if any(info.get(k)!=v for k,v in {'CFBundleExecutable':'FuxiMetal','NSPrincipalClass':'FuxiMTLDevice','CFBundleIdentifier':'org.fuxi.metal'}.items()):
        raise ValueError('unexpected plugin plist')
    macho(icd,6);macho(bundle/'FuxiMetal',6);macho(smoke,2);macho(ca_smoke,2);macho(translator,6)
    if not metallib.is_file() or metallib.stat().st_size>4*1024*1024 or metallib.read_bytes()[:4]!=b'MTLB':
        raise ValueError('expected owned-source metallib')
    if not source.is_file():raise ValueError('missing translator corresponding source archive')
    with source.open('rb') as archive:
        if archive.read(2)!=b'\x1f\x8b':raise ValueError('invalid translator corresponding source archive')
    artifacts=[(icd,'usr/local/lib/libvulkan_freedreno.dylib',0o755),
        (translator,'usr/local/lib/libmetal2vulkan.dylib',0o755),
        (metallib,'usr/local/share/fuxi/metal-smoke.metallib',0o644),
        (source,'usr/local/share/fuxi/metal2vulkan-source.tar.gz',0o644),
        (smoke,'usr/local/bin/fuxi-metal-smoke',0o755),
        (ca_smoke,'usr/local/bin/fuxi-ca-smoke',0o755),(ca_conf,'usr/local/share/fuxi/ca-smoke.conf',0o644),
        (clients,'usr/local/share/fuxi/metal-clients.conf',0o644),(conf,'usr/local/share/fuxi/metal-smoke.conf',0o644)]
    for src in ('FuxiMetal','Info.plist','LICENSE.NativeMetal','_CodeSignature/CodeResources'):
        source=bundle/src
        if not source.is_file() or source.is_symlink():raise ValueError('missing/linked bundle member')
        artifacts.append((source,'System/Library/Extensions/FuxiMetal.bundle/'+src,0o755 if src=='FuxiMetal' else 0o644))
    if any(p is not None for p in (native_bundle,native_bridge,native_source)):
        if any(p is None for p in (native_bundle,native_bridge,native_source)):
            raise ValueError('native bundle, bridge and corresponding source are required together')
        native_info=plistlib.loads((native_bundle/'Info.plist').read_bytes())
        if native_info.get('CFBundleExecutable')!='FuxiNativeMetal' or native_info.get('CFBundleIdentifier')!='com.fuxi.NativeMetal' or 'NSPrincipalClass' in native_info:
            raise ValueError('unexpected NativeMetal helper plist')
        macho(native_bundle/'FuxiNativeMetal',6);macho(native_bridge,6)
        if not native_source.is_file():raise ValueError('missing NativeMetal corresponding source archive')
        with native_source.open('rb') as archive:
            if archive.read(2)!=b'\x1f\x8b':raise ValueError('invalid NativeMetal corresponding source archive')
        artifacts.extend([(native_bridge,'usr/local/lib/libnative_metal_bridge.dylib',0o755),
                          (native_source,'usr/local/share/fuxi/nativemetal-source.tar.gz',0o644)])
        for src in ('FuxiNativeMetal','Info.plist','LICENSE.NativeMetal','_CodeSignature/CodeResources'):
            source=native_bundle/src
            if not source.is_file() or source.is_symlink():raise ValueError('missing/linked native bundle member')
            artifacts.append((source,'System/Library/Extensions/FuxiNativeMetal.bundle/'+src,0o755 if src=='FuxiNativeMetal' else 0o644))
    for source,name,mode in artifacts:
        target=root/name
        if not target.resolve().is_relative_to(root):raise ValueError('target escapes root')
        if target.is_symlink():raise ValueError('refusing linked destination')
        if not source.is_file():raise ValueError('missing artifact')
    if account is not None:prepare_backboard_storage(root,account)
    for source,name,mode in artifacts:
        target=root/name
        missing=[];parent=target.parent
        while not parent.exists():
            missing.append(parent);parent=parent.parent
        for directory in reversed(missing):
            directory.mkdir();os.chmod(directory,0o755);os.chown(directory,0,0)
        if name=='usr/local/share/fuxi/metal-clients.conf':target.write_bytes(client_data)
        else:shutil.copyfile(source,target)
        os.chmod(target,mode);os.chown(target,0,0)
        print('staged /'+name+' root:wheel %04o'%mode)


if __name__=='__main__':
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('root',type=Path);ap.add_argument('icd',type=Path);ap.add_argument('bundle',type=Path);ap.add_argument('smoke',type=Path)
    ap.add_argument('--conf',type=Path,default=Path(__file__).with_name('metal-smoke.conf'))
    ap.add_argument('--clients',type=Path,default=Path(__file__).with_name('metal-clients.conf'))
    ap.add_argument('--ca-smoke',type=Path,help='signed CA executable (default: sibling of smoke)')
    ap.add_argument('--ca-conf',type=Path,default=Path(__file__).with_name('ca-smoke.conf'))
    ap.add_argument('--translator',type=Path)
    ap.add_argument('--metallib',type=Path)
    ap.add_argument('--source',type=Path,help='external library corresponding source and licence archive')
    ap.add_argument('--native-bundle',type=Path,help='optional signed FuxiNativeMetal helper bundle')
    ap.add_argument('--native-bridge',type=Path,help='separately replaceable native Rust dylib')
    ap.add_argument('--native-source',type=Path,help='NativeMetal corresponding-source archive')
    ap.add_argument('--enable-backboardd',action='store_true',help='add backboardd allow entry and private storage; also requires boot arg fuxibbgpu=1 (2 is a diagnostic survey; may blank screen for ~20s)')
    ap.add_argument('--prepare-pipeline-cache',action='store_true',help='pre-create persistent backboardd cache storage using the target launchd UID; does not admit clients or enable fuxigpucache=1')
    a=ap.parse_args();stage(a.root,a.icd,a.bundle,a.smoke,a.conf,a.clients,a.translator,a.metallib,a.source,a.ca_smoke,a.ca_conf,a.enable_backboardd,a.native_bundle,a.native_bridge,a.native_source,a.prepare_pipeline_cache)
