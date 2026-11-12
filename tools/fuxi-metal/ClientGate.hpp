/* Original fail-closed client policy; no GPU or Objective-C dependencies. */
#pragma once
#include <cstdio>
#include <cstring>
#include <fcntl.h>
#include <sys/stat.h>
#include <unistd.h>
#ifdef __APPLE__
#include <sys/sysctl.h>
#endif
namespace fuxi {
enum class ClientMode { deny, allow, census };
static inline bool systemString(const char *key,char *out,size_t capacity) {
#ifdef __APPLE__
 size_t n=capacity;bool ok=!sysctlbyname(key,out,&n,nullptr,0) && n>0 && n<=capacity &&
  out[n-1]==0 && strlen(out)==n-1;
 if(!ok && capacity)out[0]=0;return ok;
#else
 (void)key;if(capacity)out[0]=0;return false;
#endif
}
static inline unsigned backboardMode(const char *args) {
 if(!args)return 0;unsigned keys=0,mode=0;
 for(const char *p=args;*p;) {
  p+=strspn(p," \t\r\n");size_t n=strcspn(p," \t\r\n");
  if(n>=10 && !strncmp(p,"fuxibbgpu=",10)) {++keys;mode=n==11 && (p[10]=='1' || p[10]=='2')?unsigned(p[10]-'0'):0;}
  p+=n;
 }
 return keys==1?mode:0;
}
static inline bool backboardArmed(const char *args){return backboardMode(args)!=0;}
static inline unsigned systemBackboardMode(){char args[4096]={};systemString("kern.bootargs",args,sizeof(args));return backboardMode(args);}
static inline bool clientName(const char *s) {
 size_t n=strlen(s);if(!n || n>63)return false;
 for(size_t i=0;i<n;++i)if(!((s[i]>='a'&&s[i]<='z')||(s[i]>='A'&&s[i]<='Z')||
     (s[i]>='0'&&s[i]<='9')||s[i]=='_'||s[i]=='-'||s[i]=='.'))return false;
 return true;
}
// Processes that must never see a Metal device, not even a census one. HackiOS
// (hackios-xnu-userland, MIT) observed both aborting on a visible device:
// mediaserverd's CMCapture preloads AppleCVAPhoto's shader library (28 aborts
// per boot) and SpringBoard's Siri orb prewarms its shaders (abort every
// 8.3 s). A denied device init returns nil, which is what both expect here.
// SpringBoard can be opted back in with fuxispringboard=1 for experiments.
static inline bool hiddenClient(const char *s,const char *bootargs=nullptr) {
 if(!strcmp(s,"mediaserverd"))return true;
 if(!strcmp(s,"SpringBoard"))return !(bootargs && strstr(bootargs,"fuxispringboard=1"));
 return false;
}
static inline ClientMode parseClients(char *data,size_t length,const char *process,const char *bootargs=nullptr) {
 bool backboard=!strcmp(process,"backboardd");
 if(hiddenClient(process,bootargs) || (backboard && !backboardArmed(bootargs)))return ClientMode::deny;
 if(!clientName(process)||memchr(data,0,length))return ClientMode::deny;
 data[length]=0;ClientMode result=ClientMode::deny;
 for(char *line=data;line;) {
  char *next=strchr(line,'\n');if(next)*next++=0;
  size_t n=strlen(line);while(n && (line[n-1]==' '||line[n-1]=='\t'||line[n-1]=='\r'))line[--n]=0;
  while(*line==' '||*line=='\t')++line;
  if(*line && *line!='#') {
   ClientMode mode=ClientMode::allow;
   if(!strncmp(line,"census ",7)) {mode=ClientMode::census;line+=7;while(*line==' '||*line=='\t')++line;}
   if(!clientName(line))return ClientMode::deny;
   if(!strcmp(line,process)) {
    if(result!=ClientMode::deny && result!=mode)return ClientMode::deny;
    result=mode;
   }
  }
  line=next;
 }
 return backboard && result!=ClientMode::allow?ClientMode::deny:result;
}
static inline ClientMode readClients(const char *path,const char *process) {
 int fd=open(path,O_RDONLY|O_NOFOLLOW|O_NONBLOCK);if(fd<0)return ClientMode::deny;
 struct stat st={};char data[8193];size_t used=0;bool ok=false;
 if(!fstat(fd,&st)&&S_ISREG(st.st_mode)&&st.st_uid==0&&!(st.st_mode&0022)&&st.st_size<=8192) {
  ssize_t n;while(used<sizeof(data)-1 && (n=read(fd,data+used,sizeof(data)-1-used))>0)used+=(size_t)n;
  char tail;ok=read(fd,&tail,1)==0 && used==(size_t)st.st_size;
 }
 char args[4096]={};if(!strcmp(process,"backboardd"))systemString("kern.bootargs",args,sizeof(args));
 close(fd);return ok?parseClients(data,used,process,args):ClientMode::deny;
}
}
