/* Root exec-swap diagnostic: all argv, including argv[0], is ignored. */
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#import <IOSurface/IOSurfaceRef.h>
#import <objc/runtime.h>
#include <errno.h>
#include "fuxi-metal/M7Reference.h"
#include <signal.h>
#include <setjmp.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <mach/mach.h>
/* Private libsystem_kernel call (kern_memorystatus.h). The smoke runs in place of
 * a daemon whose launchd jetsam limit is too small for a cold shader translation. */
extern int memorystatus_control(uint32_t command, int32_t pid, uint32_t flags, void *buffer, size_t size);
/* SET_JETSAM_TASK_LIMIT (6) is CONFIG_JETSAM-only and returns EINVAL on Fuxi. */
#define FUXI_MEMORYSTATUS_CMD_SET_MEMLIMIT_PROPERTIES 7
#define FUXI_MEMORYSTATUS_CMD_GET_MEMLIMIT_PROPERTIES 8
struct fuxi_memlimit {int32_t active;uint32_t active_attr;int32_t inactive;uint32_t inactive_attr;};
static unsigned long long footprint(void) {
 task_vm_info_data_t i;mach_msg_type_number_t n=TASK_VM_INFO_COUNT;
 return task_info(mach_task_self(),TASK_VM_INFO,(task_info_t)&i,&n)==KERN_SUCCESS?i.phys_footprint>>10:0;
}
#ifndef FUXI_MTL_CONF
#define FUXI_MTL_CONF "/usr/local/share/fuxi/metal-smoke.conf"
#endif
#ifndef FUXI_MTL_METALLIB
#define FUXI_MTL_METALLIB "/usr/local/share/fuxi/metal-smoke.metallib"
#endif
static unsigned stage;
static const char *name="discovery";
static char timeoutLine[192];static size_t timeoutLength;
static void expired(int sig) {(void)sig;(void)write(1,timeoutLine,timeoutLength);_exit(124);}
static void arm(unsigned seconds) {
 alarm(0);timeoutLength=(size_t)snprintf(timeoutLine,sizeof(timeoutLine),"FUXI MTL: stage %u %s FAIL exceeded %u s wall bound\n",stage,name,seconds);alarm(seconds);
}
static sigjmp_buf stageJump;static int stageArmed;
__attribute__((noreturn)) static void fail(const char *fmt,...) {
 char msg[192];va_list a;va_start(a,fmt);vsnprintf(msg,sizeof(msg),fmt,a);va_end(a);
 printf("FUXI MTL: stage %u %s FAIL %s\n",stage,name,msg);
 if(stageArmed)siglongjmp(stageJump,1);_exit(1);
}
#define STAGE(n,nm,body) do{stage=n;name=nm;arm(60);if(sigsetjmp(stageJump,1)==0){stageArmed=1;@autoreleasepool{body;}stageArmed=0;}else{stageArmed=0;printf("FUXI MTL: stage %u %s SKIP after failure\n",stage,name);}}while(0)
static unsigned configuration(void) {
 FILE *f=fopen(FUXI_MTL_CONF,"rb");if(!f){if(errno==ENOENT)return 3;fail("config unreadable");}
 char raw[32];size_t n=fread(raw,1,sizeof(raw),f);BOOL bad=ferror(f);fclose(f);
 if(bad || n==sizeof(raw))fail("config read/length");
 size_t i=0;while(i<n && strchr(" \t\r\n",raw[i]) && raw[i])++i;
 if(i==n || raw[i]<'0' || raw[i]>'9')fail("config expected max stage 0..10");
 unsigned result=(unsigned)(raw[i++]-'0');
 if(i<n && raw[i]>='0' && raw[i]<='9')result=result*10u+(unsigned)(raw[i++]-'0');
 if(result>10u)fail("config max stage %u out of range",result);
 while(i<n && raw[i] && strchr(" \t\r\n",raw[i]))++i;
 if(i!=n)fail("config trailing data");return result;
}
static void verify(id<MTLBuffer>b,NSUInteger begin,NSUInteger size,uint8_t value) {
 const uint8_t *p=b.contents;if(!p)fail("nil contents");
 for(NSUInteger i=begin;i<begin+size;++i)if(p[i]!=value)fail("byte %lu got 0x%02x want 0x%02x",(unsigned long)i,p[i],value);
}
static void finish(id<MTLCommandBuffer>command) {
 if(!command)fail("nil command buffer");
 __block unsigned completions=0;
 [command addCompletedHandler:^(id<MTLCommandBuffer>b) {if(b.status==MTLCommandBufferStatusCompleted)++completions;}];
 [command commit];[command waitUntilCompleted];
 if(command.status!=MTLCommandBufferStatusCompleted || command.error || completions!=1)
  fail("command status %lu completions %u error %s",(unsigned long)command.status,completions,command.error.description.UTF8String);
 [command addCompletedHandler:^(id<MTLCommandBuffer>b) {if(b.status==MTLCommandBufferStatusCompleted)++completions;}];
 if(completions!=2)fail("late completed handler count %u",completions);
}
static void fill(id<MTLCommandQueue>q,id<MTLBuffer>b,uint8_t value) {
 id<MTLCommandBuffer>c=q.commandBuffer;if(!c)fail("nil command buffer");
 id<MTLBlitCommandEncoder>e=c.blitCommandEncoder;if(!e)fail("nil blit encoder");
 [e fillBuffer:b range:NSMakeRange(0,b.length) value:value];[e endEncoding];finish(c);verify(b,0,b.length,value);
}
static void copy(id<MTLCommandQueue>q,id<MTLBuffer>src,id<MTLBuffer>dst,uint8_t value) {
 // CPU writes -> GPU copy -> CPU reads, with sentinel edges and nonzero offsets.
 memset(src.contents,value,src.length);memset(dst.contents,0x39,dst.length);
 id<MTLCommandBuffer>c=q.commandBuffer;if(!c)fail("nil command buffer");
 id<MTLBlitCommandEncoder>e=c.blitCommandEncoder;if(!e)fail("nil blit encoder");
 [e copyFromBuffer:src sourceOffset:4 toBuffer:dst destinationOffset:8 size:dst.length-16];
 [e endEncoding];finish(c);
 verify(dst,0,8,0x39);verify(dst,8,dst.length-16,value);verify(dst,dst.length-8,8,0x39);
}
static void pipeline(id<MTLDevice>device,id<MTLCommandQueue>q) {
 NSError *error=nil;NSURL *url=[NSURL fileURLWithPath:@FUXI_MTL_METALLIB];
 id<MTLLibrary>library=[device newLibraryWithURL:url error:&error];
 if(!library || error)fail("library URL %s",error.description.UTF8String);
 // Also exercise the public dispatch_data_t loading entry point with the same owned source.
 NSData *bytes=[NSData dataWithContentsOfURL:url];
 dispatch_data_t data=dispatch_data_create(bytes.bytes,bytes.length,dispatch_get_global_queue(0,0),DISPATCH_DATA_DESTRUCTOR_DEFAULT);
 id<MTLLibrary>dataLibrary=[device newLibraryWithData:data error:&error];dispatch_release(data);
 if(!dataLibrary || error)fail("library data %s",error.description.UTF8String);
 id<MTLFunction>v=[library newFunctionWithName:@"fuxi_vertex"],f=[dataLibrary newFunctionWithName:@"fuxi_fragment"];
 if(!v || !f || v.functionType!=MTLFunctionTypeVertex || f.functionType!=MTLFunctionTypeFragment)fail("vertex/fragment functions");
 MTLRenderPipelineDescriptor *pd=[MTLRenderPipelineDescriptor new];pd.vertexFunction=v;pd.fragmentFunction=f;
 pd.colorAttachments[0].pixelFormat=MTLPixelFormatBGRA8Unorm;
 id<MTLRenderPipelineState>p=[device newRenderPipelineStateWithDescriptor:pd error:&error];
 if(!p || error)fail("render pipeline %s",error.description.UTF8String);
 MTLTextureDescriptor *td=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatBGRA8Unorm width:256 height:256 mipmapped:NO];
 td.storageMode=MTLStorageModePrivate;td.usage=MTLTextureUsageRenderTarget;
 id<MTLTexture>t=[device newTextureWithDescriptor:td];
 id<MTLBuffer>b=[device newBufferWithLength:256*256*4+512 options:MTLResourceStorageModeShared];
 if(!t || !b)fail("texture/readback allocation");memset(b.contents,0x39,b.length);
 MTLRenderPassDescriptor *rd=[MTLRenderPassDescriptor renderPassDescriptor];rd.colorAttachments[0].texture=t;
 rd.colorAttachments[0].loadAction=MTLLoadActionClear;rd.colorAttachments[0].storeAction=MTLStoreActionStore;
 rd.colorAttachments[0].clearColor=MTLClearColorMake(0,0,0,1);
 id<MTLCommandBuffer>c=q.commandBuffer;id<MTLRenderCommandEncoder>r=[c renderCommandEncoderWithDescriptor:rd];
 if(!r)fail("nil render encoder");[r setRenderPipelineState:p];
 [r drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3];[r endEncoding];
 id<MTLBlitCommandEncoder>blit=c.blitCommandEncoder;if(!blit)fail("nil readback encoder");
 [blit copyFromTexture:t sourceSlice:0 sourceLevel:0 sourceOrigin:MTLOriginMake(0,0,0) sourceSize:MTLSizeMake(256,256,1)
 toBuffer:b destinationOffset:256 destinationBytesPerRow:1024 destinationBytesPerImage:256*256*4];
 [blit endEncoding];finish(c);verify(b,0,256,0x39);verify(b,b.length-256,256,0x39);
 const uint8_t *pixels=(const uint8_t *)b.contents+256;unsigned red=0,black=0;
 for(unsigned i=0;i<65536;++i) {
  const uint8_t *x=pixels+4*i;
  if(x[0] || x[1] || x[3]!=255 || (x[2]!=0 && x[2]!=255))fail("pixel %u BGRA %u,%u,%u,%u",i,x[0],x[1],x[2],x[3]);
  if(x[2])++red;else ++black;
 }
 const unsigned samples[][3]={{128,128,255},{96,176,255},{160,176,255},{0,0,0},{128,26,0},{64,64,0}};
 for(unsigned i=0;i<6;++i)if(pixels[(samples[i][1]*256+samples[i][0])*4+2]!=samples[i][2])fail("inside/outside sample %u",i);
 if(red<8064 || red>8320 || red+black!=65536)fail("pixel counts red %u black %u",red,black);
 printf("FUXI MTL: stage 4 pipeline PASS BGRA8 256x256 inside/outside 6 red %u clear %u + sentinel edges\n",red,black);
 // M8 compute: kernel in the main metallib; raw AIR bitcode remains a local fallback.
 error=nil;id<MTLLibrary> computeLibrary=nil;id<MTLFunction> kernel=[library newFunctionWithName:@"fuxi_compute"];
 if(!kernel){error=nil;NSURL *computeURL=[NSURL fileURLWithPath:@"/usr/local/share/fuxi/metal-compute.bc"];computeLibrary=[device newLibraryWithURL:computeURL error:&error];if(!computeLibrary || error)fail("compute library %s",error.description.UTF8String);kernel=[computeLibrary newFunctionWithName:@"fuxi_compute"];}
 if(!kernel || kernel.functionType!=MTLFunctionTypeKernel)fail("compute kernel function");
 id<MTLComputePipelineState> cp=[device newComputePipelineStateWithFunction:kernel error:&error];
 if(!cp || error)fail("compute pipeline %s",error.description.UTF8String);
 if(cp.maxTotalThreadsPerThreadgroup<64)fail("compute max threads %lu",(unsigned long)cp.maxTotalThreadsPerThreadgroup);
 id<MTLBuffer> values=[device newBufferWithLength:65536+512 options:MTLResourceStorageModeShared];
 if(!values)fail("compute buffer allocation");memset(values.contents,0x39,values.length);
 id<MTLCommandBuffer> cc=q.commandBuffer;if(!cc)fail("nil compute command buffer");
 id<MTLComputeCommandEncoder> ce=[cc computeCommandEncoder];if(!ce)fail("nil compute encoder");
 [ce setComputePipelineState:cp];[ce setBuffer:values offset:0 atIndex:0];
 [ce dispatchThreads:MTLSizeMake(16384,1,1) threadsPerThreadgroup:MTLSizeMake(64,1,1)];
 [ce endEncoding];finish(cc);
 const uint32_t *out=(const uint32_t *)values.contents;
 for(unsigned i=0;i<16384;++i)if(out[i]!=i*3u+7u)fail("compute value %u got %u",i,out[i]);
 const uint8_t *tail=(const uint8_t *)values.contents+65536;
 for(unsigned i=0;i<512;++i)if(tail[i]!=0x39)fail("compute sentinel %u",i);
 printf("FUXI MTL: stage 4 compute PASS kernel dispatchThreads 16384 threads + sentinel edges\n");
 [values release];[cp release];[kernel release];[computeLibrary release];
 [b release];[t release];[p release];[pd release];[v release];[f release];[library release];[dataLibrary release];
}
static IOSurfaceRef surface(void) {
 NSDictionary *properties=@{(id)kIOSurfaceWidth:@256,(id)kIOSurfaceHeight:@256,
  (id)kIOSurfaceBytesPerElement:@4,(id)kIOSurfaceBytesPerRow:@1024,
  (id)kIOSurfacePixelFormat:@(0x42475241),(id)kIOSurfaceAllocSize:@262144};
 IOSurfaceRef result=IOSurfaceCreate((CFDictionaryRef)properties);
 if(!result)fail("IOSurfaceCreate");return result;
}
static void texel(unsigned x,unsigned y,uint8_t *out) {
 // Four different opaque quadrants expose channel swaps, stale reads and flips.
 out[0]=y>=128?255:0;out[1]=x>=128?255:0;out[2]=y<128 && x<128?255:0;out[3]=255;
}
static void composition(id<MTLDevice>device,id<MTLCommandQueue>q) {
 IOSurfaceRef src=surface(),dst=surface();
 if(IOSurfaceLock(src,0,NULL))fail("IOSurface source lock");
 uint8_t *base=IOSurfaceGetBaseAddress(src);size_t row=IOSurfaceGetBytesPerRow(src);
 if(!base || row<1024)fail("IOSurface source mapping/stride");
 for(unsigned y=0;y<256;++y)for(unsigned x=0;x<256;++x)texel(x,y,base+y*row+x*4);
 if(IOSurfaceUnlock(src,0,NULL))fail("IOSurface source unlock");
 MTLTextureDescriptor *td=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatBGRA8Unorm width:256 height:256 mipmapped:NO];
 td.storageMode=MTLStorageModeShared;td.usage=MTLTextureUsageShaderRead|MTLTextureUsageRenderTarget;
 id<MTLTexture>input=[device newTextureWithDescriptor:td iosurface:src plane:0];
 if(!input)fail("IOSurface source import unavailable (same-memory required)");
 id<MTLTexture>target=[device newTextureWithDescriptor:td iosurface:dst plane:0];
 if(!target || input.iosurface!=src || target.iosurface!=dst || input.iosurfacePlane || target.iosurfacePlane)fail("IOSurface target/identity");
 NSError *error=nil;id<MTLLibrary>lib=[device newLibraryWithURL:[NSURL fileURLWithPath:@FUXI_MTL_METALLIB] error:&error];
 if(!lib || error)fail("quad library %s",error.description.UTF8String);
 id<MTLFunction>v=[lib newFunctionWithName:@"fuxi_quad"],f=[lib newFunctionWithName:@"fuxi_sample"];
 if(!v || !f)fail("quad functions");
 MTLRenderPipelineDescriptor *pd=[MTLRenderPipelineDescriptor new];pd.vertexFunction=v;pd.fragmentFunction=f;pd.colorAttachments[0].pixelFormat=MTLPixelFormatBGRA8Unorm;
 id<MTLRenderPipelineState>p=[device newRenderPipelineStateWithDescriptor:pd error:&error];
 if(!p || error)fail("sampled pipeline %s",error.description.UTF8String);
 // The second creation must use the translator's process cache.
 id<MTLRenderPipelineState>cached=[device newRenderPipelineStateWithDescriptor:pd error:&error];
 if(!cached || error)fail("cached sampled pipeline");[cached release];
 MTLSamplerDescriptor *sd=[MTLSamplerDescriptor new];id<MTLSamplerState>sampler=[device newSamplerStateWithDescriptor:sd];
 id<MTLBuffer>readback=[device newBufferWithLength:262656 options:MTLResourceStorageModeShared];
 if(!sampler || !readback)fail("sampler/readback allocation");memset(readback.contents,0x39,readback.length);
 @autoreleasepool {
  MTLRenderPassDescriptor *pass=[MTLRenderPassDescriptor renderPassDescriptor];pass.colorAttachments[0].texture=target;
  pass.colorAttachments[0].loadAction=MTLLoadActionClear;pass.colorAttachments[0].storeAction=MTLStoreActionStore;
  pass.colorAttachments[0].clearColor=MTLClearColorMake(0,0,0,1);
  id<MTLCommandBuffer>command=q.commandBuffer;id<MTLRenderCommandEncoder>r=[command renderCommandEncoderWithDescriptor:pass];
  if(!r)fail("nil sampled render encoder");[r setRenderPipelineState:p];[r setFragmentTexture:input atIndex:0];[r setFragmentSamplerState:sampler atIndex:0];
  [r drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:6];[r endEncoding];
  id<MTLBlitCommandEncoder>b=command.blitCommandEncoder;if(!b)fail("nil sampled readback encoder");
  [b copyFromTexture:target sourceSlice:0 sourceLevel:0 sourceOrigin:MTLOriginMake(0,0,0) sourceSize:MTLSizeMake(256,256,1)
   toBuffer:readback destinationOffset:256 destinationBytesPerRow:1024 destinationBytesPerImage:262144];[b endEncoding];finish(command);
 }
 verify(readback,0,256,0x39);verify(readback,262400,256,0x39);
 const uint8_t *pixels=(const uint8_t *)readback.contents+256;
 for(unsigned y=0;y<256;++y)for(unsigned x=0;x<256;++x) {uint8_t want[4];texel(x,y,want);
  if(memcmp(pixels+(y*256+x)*4,want,4))fail("sampled pixel %u,%u",x,y);
 }
 [input release];[target release]; // Release import locks before the CPU consumer.
 if(IOSurfaceLock(dst,kIOSurfaceLockReadOnly,NULL))fail("IOSurface target lock");
 base=IOSurfaceGetBaseAddress(dst);row=IOSurfaceGetBytesPerRow(dst);if(!base || row<1024)fail("IOSurface target mapping/stride");
 for(unsigned y=0;y<256;++y)if(memcmp(base+y*row,pixels+y*1024,1024))fail("IOSurface same-memory row %u",y);
 if(IOSurfaceUnlock(dst,kIOSurfaceLockReadOnly,NULL))fail("IOSurface target unlock");
 puts("FUXI MTL: stage 5 composition PASS IOSurface BGRA8 256x256 textured quad 65536 pixels + same-memory target + sentinel edges");
 [readback release];[sampler release];[sd release];[p release];[pd release];[v release];[f release];[lib release];CFRelease(src);CFRelease(dst);
}
static void layers(id<MTLDevice>device,id<MTLCommandQueue>q) {
 IOSurfaceRef surfaces[3]={surface(),surface(),surface()};id<MTLTexture>textures[3]={nil,nil,nil};
 MTLTextureDescriptor *td=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatBGRA8Unorm width:256 height:256 mipmapped:NO];
 td.storageMode=MTLStorageModeShared;td.usage=MTLTextureUsageShaderRead|MTLTextureUsageRenderTarget;
 for(unsigned i=0;i<3;++i){if(IOSurfaceLock(surfaces[i],0,NULL))fail("layer surface lock");
  uint8_t *base=IOSurfaceGetBaseAddress(surfaces[i]);size_t row=IOSurfaceGetBytesPerRow(surfaces[i]);if(!base || row<1024)fail("layer mapping");
  for(unsigned y=0;y<256;++y)for(unsigned x=0;x<256;++x){if(i<2)fuxiM7Texel(i,x,y,base+y*row+x*4);else memset(base+y*row+x*4,0x39,4);}
  if(IOSurfaceUnlock(surfaces[i],0,NULL))fail("layer surface unlock");
  textures[i]=[device newTextureWithDescriptor:td iosurface:surfaces[i] plane:0];if(!textures[i])fail("layer same-memory import");
 }
 NSError *error=nil;id<MTLLibrary>lib=[device newLibraryWithURL:[NSURL fileURLWithPath:@FUXI_MTL_METALLIB] error:&error];if(!lib || error)fail("layer library");
 MTLFunctionConstantValues *values=[MTLFunctionConstantValues new];BOOL enabled=YES;[values setConstantValue:&enabled type:MTLDataTypeBool atIndex:0];
 id<MTLFunction>v=[lib newFunctionWithName:@"fuxi_layer_vertex"],f=[lib newFunctionWithName:@"fuxi_layer_fragment" constantValues:values error:&error];
 if(!v || !f || error)fail("layer function constants %s",error.description.UTF8String);
 MTLRenderPipelineDescriptor *pd=[MTLRenderPipelineDescriptor new];pd.vertexFunction=v;pd.fragmentFunction=f;
 MTLVertexDescriptor *vd=[MTLVertexDescriptor vertexDescriptor];
 vd.attributes[0].format=MTLVertexFormatFloat2;vd.attributes[0].bufferIndex=0;
 vd.attributes[1].format=MTLVertexFormatFloat2;vd.attributes[1].offset=8;vd.attributes[1].bufferIndex=0;
 vd.attributes[2].format=MTLVertexFormatUChar4Normalized;vd.attributes[2].offset=16;vd.attributes[2].bufferIndex=0;vd.layouts[0].stride=sizeof(struct FuxiM7VertexData);pd.vertexDescriptor=vd;
 MTLRenderPipelineColorAttachmentDescriptor *color=pd.colorAttachments[0];color.pixelFormat=MTLPixelFormatBGRA8Unorm;color.blendingEnabled=YES;
 color.sourceRGBBlendFactor=color.sourceAlphaBlendFactor=MTLBlendFactorOne;
 color.destinationRGBBlendFactor=color.destinationAlphaBlendFactor=MTLBlendFactorOneMinusSourceAlpha;
 id<MTLRenderPipelineState>p=[device newRenderPipelineStateWithDescriptor:pd error:&error];if(!p || error)fail("layer pipeline %s",error.description.UTF8String);
 id<MTLRenderPipelineState>cached=[device newRenderPipelineStateWithDescriptor:pd error:&error];if(!cached || error)fail("layer cache");[cached release];
 id<MTLBuffer>vertices=[device newBufferWithBytes:fuxiM7Vertices length:sizeof(fuxiM7Vertices) options:MTLResourceStorageModeShared];
 id<MTLBuffer>indices=[device newBufferWithBytes:fuxiM7Indices length:sizeof(fuxiM7Indices) options:MTLResourceStorageModeShared];
 id<MTLBuffer>readback=[device newBufferWithLength:262656 options:MTLResourceStorageModeShared];
 MTLSamplerDescriptor *sd=[MTLSamplerDescriptor new];id<MTLSamplerState>sampler=[device newSamplerStateWithDescriptor:sd];
 if(!vertices || !indices || !readback || !sampler)fail("layer buffers/sampler");memset(readback.contents,0x39,readback.length);
 @autoreleasepool {id<MTLCommandBuffer>command=q.commandBuffer;
  for(unsigned i=0;i<2;++i){MTLRenderPassDescriptor *pass=[MTLRenderPassDescriptor renderPassDescriptor];pass.colorAttachments[0].texture=textures[2];
   pass.colorAttachments[0].loadAction=i?MTLLoadActionLoad:MTLLoadActionClear;pass.colorAttachments[0].storeAction=MTLStoreActionStore;pass.colorAttachments[0].clearColor=MTLClearColorMake(0,0,0,1);
   id<MTLRenderCommandEncoder>r=[command renderCommandEncoderWithDescriptor:pass];if(!r)fail("layer pass");
   [r setRenderPipelineState:p];[r setVertexBuffer:vertices offset:0 atIndex:0];[r setFragmentTexture:textures[i] atIndex:0];[r setFragmentSamplerState:sampler atIndex:0];
   float geometry[4]={1,1,1,1};[r setVertexBytes:geometry length:sizeof(geometry) atIndex:2];memset(geometry,0,sizeof(geometry));
   float tint[4]={i?0.5f:1.0f,i?0.5f:1.0f,i?0.5f:1.0f,i?0.5f:1.0f};[r setFragmentBytes:tint length:sizeof(tint) atIndex:1];memset(tint,0,sizeof(tint));
   [r setViewport:(MTLViewport){0,0,256,256,0,1}];if(i)[r setScissorRect:(MTLScissorRect){16,24,224,208}];
   [r drawIndexedPrimitives:MTLPrimitiveTypeTriangle indexCount:6 indexType:MTLIndexTypeUInt16 indexBuffer:indices indexBufferOffset:0];[r endEncoding];
  }
  id<MTLBlitCommandEncoder>b=command.blitCommandEncoder;if(!b)fail("layer readback");
  [b copyFromTexture:textures[2] sourceSlice:0 sourceLevel:0 sourceOrigin:MTLOriginMake(0,0,0) sourceSize:MTLSizeMake(256,256,1)
   toBuffer:readback destinationOffset:256 destinationBytesPerRow:1024 destinationBytesPerImage:262144];[b endEncoding];finish(command);
 }
 verify(readback,0,256,0x39);verify(readback,262400,256,0x39);const uint8_t *pixels=(const uint8_t *)readback.contents+256;
 for(unsigned y=0;y<256;++y)for(unsigned x=0;x<256;++x){uint8_t want[4];fuxiM7Reference(x,y,want);if(memcmp(pixels+4*(y*256+x),want,4))fail("layer pixel %u,%u",x,y);}
 for(unsigned i=0;i<3;++i)[textures[i] release];
 if(IOSurfaceLock(surfaces[2],kIOSurfaceLockReadOnly,NULL))fail("layer consumer lock");
 const uint8_t *base=IOSurfaceGetBaseAddress(surfaces[2]);size_t row=IOSurfaceGetBytesPerRow(surfaces[2]);
 for(unsigned y=0;y<256;++y)if(memcmp(base+y*row,pixels+y*1024,1024))fail("layer same-memory row %u",y);
 if(IOSurfaceUnlock(surfaces[2],kIOSurfaceLockReadOnly,NULL))fail("layer consumer unlock");
 puts("FUXI MTL: stage 6 layers PASS IOSurface BGRA8 source-over + vertex/index buffers + function constant + fragment bytes + two passes + scissor 65536 pixels + same-memory target + sentinel edges");
 [sampler release];[sd release];[readback release];[indices release];[vertices release];[p release];[pd release];[v release];[f release];[values release];[lib release];for(unsigned i=0;i<3;++i)CFRelease(surfaces[i]);
}
// M9 device coverage: a sampled view of one mip level generated on the GPU.
static void mips(id<MTLDevice>device,id<MTLCommandQueue>q) {
 MTLTextureDescriptor *td=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatBGRA8Unorm width:64 height:64 mipmapped:YES];
 td.storageMode=MTLStorageModeShared;td.usage=MTLTextureUsageShaderRead|MTLTextureUsageRenderTarget;
 id<MTLTexture>source=[device newTextureWithDescriptor:td];if(!source)fail("mip source allocation");
 if(source.mipmapLevelCount<4)fail("mip level count %lu",(unsigned long)source.mipmapLevelCount);
 static const uint8_t want[4]={0x11,0x22,0x33,0xff};
 uint8_t base[64*64*4];for(size_t i=0;i<sizeof(base);i+=4)memcpy(base+i,want,4);
 [source replaceRegion:MTLRegionMake2D(0,0,64,64) mipmapLevel:0 withBytes:base bytesPerRow:256];
 id<MTLCommandBuffer>command=q.commandBuffer;id<MTLBlitCommandEncoder>b=command.blitCommandEncoder;
 if(!b)fail("mip blit encoder");[b generateMipmapsForTexture:source];[b endEncoding];finish(command);
 id<MTLBuffer>readback=[device newBufferWithLength:1536 options:MTLResourceStorageModeShared];
 if(!readback)fail("mip readback allocation");memset(readback.contents,0x39,readback.length);
 command=q.commandBuffer;b=command.blitCommandEncoder;
 [b copyFromTexture:source sourceSlice:0 sourceLevel:2 sourceOrigin:MTLOriginMake(0,0,0) sourceSize:MTLSizeMake(16,16,1)
  toBuffer:readback destinationOffset:256 destinationBytesPerRow:64 destinationBytesPerImage:1024];[b endEncoding];finish(command);
 verify(readback,0,256,0x39);verify(readback,1280,256,0x39);
 const uint8_t *mip=(const uint8_t *)readback.contents+256;
 for(unsigned i=0;i<16*16;++i)if(memcmp(mip+i*4,want,4))fail("mip level 2 texel %u",i);
 id<MTLTexture>view=[source newTextureViewWithPixelFormat:MTLPixelFormatBGRA8Unorm textureType:MTLTextureType2D
  levels:NSMakeRange(2,1) slices:NSMakeRange(0,1)];
 MTLTextureDescriptor *targetDescriptor=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatBGRA8Unorm width:16 height:16 mipmapped:NO];
 targetDescriptor.storageMode=MTLStorageModeShared;targetDescriptor.usage=MTLTextureUsageShaderRead|MTLTextureUsageRenderTarget;
 id<MTLTexture>target=[device newTextureWithDescriptor:targetDescriptor];
 if(!view || !target)fail("mip level view/target");
 NSError *error=nil;id<MTLLibrary>lib=[device newLibraryWithURL:[NSURL fileURLWithPath:@FUXI_MTL_METALLIB] error:&error];
 if(!lib || error)fail("mip library %s",error.description.UTF8String);
 id<MTLFunction>v=[lib newFunctionWithName:@"fuxi_quad"],f=[lib newFunctionWithName:@"fuxi_quad_sample"];
 if(!v || !f)fail("mip functions");
 MTLRenderPipelineDescriptor *pd=[MTLRenderPipelineDescriptor new];pd.vertexFunction=v;pd.fragmentFunction=f;pd.colorAttachments[0].pixelFormat=MTLPixelFormatBGRA8Unorm;
 id<MTLRenderPipelineState>p=[device newRenderPipelineStateWithDescriptor:pd error:&error];
 if(!p || error)fail("mip pipeline %s",error.description.UTF8String);
 MTLSamplerDescriptor *sd=[MTLSamplerDescriptor new];id<MTLSamplerState>sampler=[device newSamplerStateWithDescriptor:sd];
 if(!sampler)fail("mip sampler");
 memset(readback.contents,0x39,readback.length);
 @autoreleasepool {
  MTLRenderPassDescriptor *pass=[MTLRenderPassDescriptor renderPassDescriptor];pass.colorAttachments[0].texture=target;
  pass.colorAttachments[0].loadAction=MTLLoadActionClear;pass.colorAttachments[0].storeAction=MTLStoreActionStore;
  pass.colorAttachments[0].clearColor=MTLClearColorMake(0,0,0,1);
  command=q.commandBuffer;id<MTLRenderCommandEncoder>r=[command renderCommandEncoderWithDescriptor:pass];if(!r)fail("mip pass");
  [r setRenderPipelineState:p];[r setFragmentTexture:view atIndex:0];[r setFragmentSamplerState:sampler atIndex:0];
  float inverse[2]={1.0f/16.0f,1.0f/16.0f};[r setFragmentBytes:inverse length:sizeof(inverse) atIndex:0];
  [r drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:6];[r endEncoding];
  b=command.blitCommandEncoder;if(!b)fail("mip sampled readback");
  [b copyFromTexture:target sourceSlice:0 sourceLevel:0 sourceOrigin:MTLOriginMake(0,0,0) sourceSize:MTLSizeMake(16,16,1)
   toBuffer:readback destinationOffset:256 destinationBytesPerRow:64 destinationBytesPerImage:1024];[b endEncoding];finish(command);
 }
 verify(readback,0,256,0x39);verify(readback,1280,256,0x39);
 const uint8_t *sampled=(const uint8_t *)readback.contents+256;
 for(unsigned i=0;i<16*16;++i)if(memcmp(sampled+i*4,want,4))fail("mip sampled texel %u",i);
 [sampler release];[sd release];[p release];[pd release];[v release];[f release];[lib release];
 [target release];[view release];[readback release];[source release];
 puts("FUXI MTL: stage 7 mips PASS generateMipmaps 64x64 -> level 2 (16x16) copy 256 texels + level 2 view sampled 256 texels");
}
// M9 device coverage: the ten-level chain CA asks for, generated and read at the tail.
static void mipchain(id<MTLDevice>device,id<MTLCommandQueue>q) {
 MTLTextureDescriptor *td=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatBGRA8Unorm width:512 height:512 mipmapped:YES];
 td.storageMode=MTLStorageModeShared;td.usage=MTLTextureUsageShaderRead|MTLTextureUsageRenderTarget;
 id<MTLTexture>source=[device newTextureWithDescriptor:td];if(!source)fail("mipchain source allocation");
 if(source.mipmapLevelCount!=10)fail("mipchain level count %lu",(unsigned long)source.mipmapLevelCount);
 static const uint8_t want[4]={0x51,0x62,0x73,0xff};
 uint8_t *base=(uint8_t *)malloc(512*512*4);if(!base)fail("mipchain base allocation");
 for(size_t i=0;i<512*512*4;i+=4)memcpy(base+i,want,4);
 [source replaceRegion:MTLRegionMake2D(0,0,512,512) mipmapLevel:0 withBytes:base bytesPerRow:2048];free(base);
 id<MTLCommandBuffer>command=q.commandBuffer;id<MTLBlitCommandEncoder>b=command.blitCommandEncoder;
 if(!b)fail("mipchain blit encoder");[b generateMipmapsForTexture:source];[b endEncoding];finish(command);
 id<MTLBuffer>readback=[device newBufferWithLength:768 options:MTLResourceStorageModeShared];
 if(!readback)fail("mipchain readback allocation");memset(readback.contents,0x39,readback.length);
 command=q.commandBuffer;b=command.blitCommandEncoder;if(!b)fail("mipchain tail encoder");
 [b copyFromTexture:source sourceSlice:0 sourceLevel:9 sourceOrigin:MTLOriginMake(0,0,0) sourceSize:MTLSizeMake(1,1,1)
  toBuffer:readback destinationOffset:256 destinationBytesPerRow:4 destinationBytesPerImage:4];[b endEncoding];finish(command);
 verify(readback,0,256,0x39);verify(readback,260,768-260,0x39);
 const uint8_t *tail=(const uint8_t *)readback.contents+256;
 if(memcmp(tail,want,4))fail("mipchain level 9 texel %02x%02x%02x%02x",tail[0],tail[1],tail[2],tail[3]);
 [readback release];[source release];
 puts("FUXI MTL: stage 10 mipchain PASS 512x512 mipmapped gives 10 levels, generateMipmaps, level 9 (1x1) copied");
}
// M9 device coverage: three color targets carrying different formats at once.
static void mrt(id<MTLDevice>device,id<MTLCommandQueue>q) {
 const MTLPixelFormat formats[3]={MTLPixelFormatBGRA8Unorm,MTLPixelFormatRGBA16Float,MTLPixelFormatRGBA8Unorm};
 id<MTLTexture>targets[3]={nil,nil,nil};
 MTLRenderPassDescriptor *pass=[MTLRenderPassDescriptor renderPassDescriptor];
 MTLRenderPipelineDescriptor *pd=[MTLRenderPipelineDescriptor new];
 for(unsigned i=0;i<3;++i) {
  MTLTextureDescriptor *td=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:formats[i] width:32 height:32 mipmapped:NO];
  td.storageMode=MTLStorageModeShared;td.usage=MTLTextureUsageShaderRead|MTLTextureUsageRenderTarget;
  targets[i]=[device newTextureWithDescriptor:td];if(!targets[i])fail("mrt target %u allocation",i);
  MTLRenderPassColorAttachmentDescriptor *a=pass.colorAttachments[i];
  a.texture=targets[i];a.loadAction=MTLLoadActionClear;a.storeAction=MTLStoreActionStore;
  a.clearColor=MTLClearColorMake(0,0,0,1);pd.colorAttachments[i].pixelFormat=formats[i];
 }
 NSError *error=nil;id<MTLLibrary>lib=[device newLibraryWithURL:[NSURL fileURLWithPath:@FUXI_MTL_METALLIB] error:&error];
 if(!lib || error)fail("mrt library %s",error.description.UTF8String);
 id<MTLFunction>v=[lib newFunctionWithName:@"fuxi_quad"],f=[lib newFunctionWithName:@"fuxi_mrt_fragment"];
 if(!v || !f)fail("mrt functions");
 pd.vertexFunction=v;pd.fragmentFunction=f;
 id<MTLRenderPipelineState>p=[device newRenderPipelineStateWithDescriptor:pd error:&error];
 if(!p || error)fail("mrt pipeline %s",error.description.UTF8String);
 @autoreleasepool {
  id<MTLCommandBuffer>command=q.commandBuffer;id<MTLRenderCommandEncoder>r=[command renderCommandEncoderWithDescriptor:pass];
  if(!r)fail("mrt pass");[r setRenderPipelineState:p];[r drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:6];[r endEncoding];finish(command);
 }
 const uint8_t want0[4]={0x00,0x00,0xff,0xff};   // BGRA8 c0 = red
 const uint8_t want1[8]={0x00,0x00,0x00,0x38,0x00,0x00,0x00,0x3c}; // RGBA16Float c1 = (0,.5,0,1)
 const uint8_t want2[4]={0x00,0x00,0xff,0xff};   // RGBA8 c2 = blue
 for(unsigned i=0;i<3;++i) {
  unsigned size=i==1?8:4;size_t pixelBytes=32*32*size;
  id<MTLBuffer>readback=[device newBufferWithLength:pixelBytes+512 options:MTLResourceStorageModeShared];
  if(!readback)fail("mrt readback %u allocation",i);memset(readback.contents,0x39,pixelBytes+512);
  id<MTLCommandBuffer>command=q.commandBuffer;id<MTLBlitCommandEncoder>b=command.blitCommandEncoder;if(!b)fail("mrt readback %u encoder",i);
  [b copyFromTexture:targets[i] sourceSlice:0 sourceLevel:0 sourceOrigin:MTLOriginMake(0,0,0) sourceSize:MTLSizeMake(32,32,1)
   toBuffer:readback destinationOffset:256 destinationBytesPerRow:32*size destinationBytesPerImage:pixelBytes];[b endEncoding];finish(command);
  verify(readback,0,256,0x39);verify(readback,256+pixelBytes,256,0x39);
  const uint8_t *pixels=(const uint8_t *)readback.contents+256;
  int matches=1;
  for(uint32_t texel=0;texel<32*32;++texel) {
   const uint8_t *want=i==1?want1:(i==0?want0:want2);
   if(memcmp(pixels+texel*size,want,size)){matches=0;break;}
  }
  if(!matches)fail("mrt target %u texel mismatch",i);
  [readback release];
 }
 for(unsigned i=0;i<3;++i)[targets[i] release];
 [p release];[pd release];[v release];[f release];[lib release];
 puts("FUXI MTL: stage 8 mrt PASS BGRA8 + RGBA16Float + RGBA8 targets 1024 texels each");
}
// M9 device coverage: 3D texture upload, plane copy and slice sampling.
static void volume(id<MTLDevice>device,id<MTLCommandQueue>q) {
 MTLTextureDescriptor *td=[MTLTextureDescriptor new];
 td.textureType=MTLTextureType3D;td.pixelFormat=MTLPixelFormatRGBA8Unorm;
 td.width=16;td.height=16;td.depth=4;td.usage=MTLTextureUsageShaderRead;td.storageMode=MTLStorageModeShared;
 id<MTLTexture>texture=[device newTextureWithDescriptor:td];if(!texture)fail("volume allocation");
 if(texture.depth!=4 || texture.textureType!=MTLTextureType3D)fail("volume shape");
 uint8_t planes[4][16*16*4];
 for(unsigned z=0;z<4;++z)for(size_t i=0;i<sizeof(planes[0]);i+=4) {
  planes[z][i]=(uint8_t)(z*64+1);planes[z][i+1]=(uint8_t)(z*64+2);planes[z][i+2]=(uint8_t)(z*64+3);planes[z][i+3]=0xff;
 }
 for(unsigned z=0;z<4;++z)
  [texture replaceRegion:MTLRegionMake3D(0,0,z,16,16,1) mipmapLevel:0 slice:0 withBytes:planes[z] bytesPerRow:64 bytesPerImage:1024];
 for(unsigned z=0;z<4;++z) {
  id<MTLBuffer>readback=[device newBufferWithLength:1536 options:MTLResourceStorageModeShared];
  if(!readback)fail("volume readback allocation");memset(readback.contents,0x39,readback.length);
  id<MTLCommandBuffer>command=q.commandBuffer;id<MTLBlitCommandEncoder>b=command.blitCommandEncoder;if(!b)fail("volume readback encoder");
  [b copyFromTexture:texture sourceSlice:0 sourceLevel:0 sourceOrigin:MTLOriginMake(0,0,z) sourceSize:MTLSizeMake(16,16,1)
   toBuffer:readback destinationOffset:256 destinationBytesPerRow:64 destinationBytesPerImage:1024];[b endEncoding];finish(command);
  verify(readback,0,256,0x39);verify(readback,1280,256,0x39);
  const uint8_t *plane=(const uint8_t *)readback.contents+256;
  for(unsigned i=0;i<16*16;++i)if(memcmp(plane+i*4,planes[z]+i*4,4))fail("volume plane %u texel %u",z,i);
  [readback release];
 }
 MTLTextureDescriptor *targetDescriptor=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA8Unorm width:16 height:16 mipmapped:NO];
 targetDescriptor.storageMode=MTLStorageModeShared;targetDescriptor.usage=MTLTextureUsageShaderRead|MTLTextureUsageRenderTarget;
 id<MTLTexture>target=[device newTextureWithDescriptor:targetDescriptor];if(!target)fail("volume target allocation");
 NSError *error=nil;id<MTLLibrary>lib=[device newLibraryWithURL:[NSURL fileURLWithPath:@FUXI_MTL_METALLIB] error:&error];
 if(!lib || error)fail("volume library %s",error.description.UTF8String);
 id<MTLFunction>v=[lib newFunctionWithName:@"fuxi_quad"],f=[lib newFunctionWithName:@"fuxi_volume_sample"];
 if(!v || !f)fail("volume functions");
 MTLRenderPipelineDescriptor *pd=[MTLRenderPipelineDescriptor new];pd.vertexFunction=v;pd.fragmentFunction=f;pd.colorAttachments[0].pixelFormat=MTLPixelFormatRGBA8Unorm;
 id<MTLRenderPipelineState>p=[device newRenderPipelineStateWithDescriptor:pd error:&error];
 if(!p || error)fail("volume pipeline %s",error.description.UTF8String);
 MTLSamplerDescriptor *sd=[MTLSamplerDescriptor new];id<MTLSamplerState>sampler=[device newSamplerStateWithDescriptor:sd];
 if(!sampler)fail("volume sampler");
 id<MTLBuffer>readback=[device newBufferWithLength:1536 options:MTLResourceStorageModeShared];
 if(!readback)fail("volume sampled readback allocation");memset(readback.contents,0x39,readback.length);
 @autoreleasepool {
  MTLRenderPassDescriptor *pass=[MTLRenderPassDescriptor renderPassDescriptor];pass.colorAttachments[0].texture=target;
  pass.colorAttachments[0].loadAction=MTLLoadActionClear;pass.colorAttachments[0].storeAction=MTLStoreActionStore;
  pass.colorAttachments[0].clearColor=MTLClearColorMake(0,0,0,1);
  id<MTLCommandBuffer>command=q.commandBuffer;id<MTLRenderCommandEncoder>r=[command renderCommandEncoderWithDescriptor:pass];if(!r)fail("volume pass");
  [r setRenderPipelineState:p];[r setFragmentTexture:texture atIndex:0];[r setFragmentSamplerState:sampler atIndex:0];
  float slice=(2.0f+0.5f)/4.0f;[r setFragmentBytes:&slice length:sizeof(slice) atIndex:0];
  float inverse[2]={1.0f/16.0f,1.0f/16.0f};[r setFragmentBytes:inverse length:sizeof(inverse) atIndex:1];
  [r drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:6];[r endEncoding];
  id<MTLBlitCommandEncoder>b=command.blitCommandEncoder;if(!b)fail("volume sampled readback encoder");
  [b copyFromTexture:target sourceSlice:0 sourceLevel:0 sourceOrigin:MTLOriginMake(0,0,0) sourceSize:MTLSizeMake(16,16,1)
   toBuffer:readback destinationOffset:256 destinationBytesPerRow:64 destinationBytesPerImage:1024];[b endEncoding];finish(command);
 }
 verify(readback,0,256,0x39);verify(readback,1280,256,0x39);
 const uint8_t *pixels=(const uint8_t *)readback.contents+256;
 for(unsigned i=0;i<16*16;++i)if(memcmp(pixels+i*4,planes[2]+i*4,4))fail("volume sampled texel %u",i);
 [readback release];[sampler release];[sd release];[p release];[pd release];[v release];[f release];[lib release];
 [target release];[texture release];
 puts("FUXI MTL: stage 9 volume PASS 16x16x4 RGBA8 planes 1024 texels + slice 2 sampled 256 texels");
}
int main(int argc,char **argv) {
 (void)argc;(void)argv;setvbuf(stdout,NULL,_IONBF,0);setvbuf(stderr,NULL,_IONBF,0);signal(SIGALRM,expired);arm(30);
 @autoreleasepool { @try {
  if(geteuid()!=0)fail("requires unsandboxed root");unsigned maximum=configuration();
  {struct fuxi_memlimit before={0},limit={1024,0,1024,0};int g=memorystatus_control(FUXI_MEMORYSTATUS_CMD_GET_MEMLIMIT_PROPERTIES,getpid(),0,&before,sizeof(before));
   int r=memorystatus_control(FUXI_MEMORYSTATUS_CMD_SET_MEMLIMIT_PROPERTIES,getpid(),0,&limit,sizeof(limit));int e=r?errno:0;
   printf("FUXI MTL: jetsam limit before %d/%d MB attr %u/%u (rc %d) -> 1024 MB non-fatal rc %d errno %d footprint %llu KiB\n",before.active,before.inactive,before.active_attr,before.inactive_attr,g,r,e,footprint());}
  unsigned pollSeconds=0;{FILE *pf=fopen("/usr/local/share/fuxi/metal-smoke.poll","rb");if(pf){if(fscanf(pf,"%u",&pollSeconds)!=1)pollSeconds=0;fclose(pf);}}
 if(pollSeconds>240)pollSeconds=240;if(pollSeconds)arm(pollSeconds+120);
 id<MTLDevice>device=nil;for(unsigned waited=0;;++waited){device=MTLCreateSystemDefaultDevice();if(device)break;if(waited*2>=pollSeconds)break;if(!waited)printf("FUXI MTL: device not ready, polling up to %u s\n",pollSeconds);sleep(2);}
 if(!device)fail("MTLCreateSystemDefaultDevice returned nil");
  Class base=objc_getClass("_MTLDevice");
  if(!base || class_getInstanceSize(base)!=528 || strcmp(class_getName(object_getClass(device)),"FuxiMTLDevice") || ![(id)device isKindOfClass:base] || !device.registryID)fail("class/size/registryID guard");
  printf("FUXI MTL: stage 0 discovery PASS name %s class %s base-size %zu instance-size %zu registryID %llu\n",device.name.UTF8String,class_getName(object_getClass(device)),class_getInstanceSize(base),class_getInstanceSize(object_getClass(device)),device.registryID);
  if(maximum) {
   stage=1;name="fill";arm(30);id<MTLCommandQueue>q=[device newCommandQueue];
   id<MTLBuffer>a=[device newBufferWithLength:65536 options:MTLResourceStorageModeShared];
   if(!q || !a || a.length!=65536 || a.storageMode!=MTLStorageModeShared)fail("queue/shared buffer");
   @autoreleasepool {fill(q,a,0xa7);}printf("FUXI MTL: stage 1 fill PASS verified 65536 bytes + completion handlers\n");
   if(maximum>=2) {
    stage=2;name="copy";arm(30);uint8_t initial[65536];memset(initial,0x52,sizeof(initial));
    id<MTLBuffer>b=[device newBufferWithBytes:initial length:sizeof(initial) options:MTLResourceStorageModeShared];
    if(!b)fail("newBufferWithBytes");verify(b,0,b.length,0x52);
    @autoreleasepool {copy(q,a,b,0x63);}printf("FUXI MTL: stage 2 copy PASS verified CPU-write/GPU-copy/readback + sentinel edges\n");
    if(maximum>=3) {stage=3;name="reuse";arm(60);
     for(unsigned i=0;i<16;++i) {@autoreleasepool {fill(q,a,(uint8_t)(i+1));copy(q,a,b,(uint8_t)(0x80+i));}}
     printf("FUXI MTL: stage 3 reuse PASS verified 16 fill/copy rounds\n");
    }
    [b release];
   }
   if(maximum>=4) {STAGE(4,"pipeline",pipeline(device,q));printf("FUXI MTL: stage 4 footprint %llu KiB\n",footprint());}
   if(maximum>=5) {STAGE(5,"composition",composition(device,q));printf("FUXI MTL: stage 5 footprint %llu KiB\n",footprint());}
   if(maximum>=6) {STAGE(6,"layers",layers(device,q));printf("FUXI MTL: stage 6 footprint %llu KiB\n",footprint());}
   if(maximum>=7) {STAGE(7,"mips",mips(device,q));printf("FUXI MTL: stage 7 footprint %llu KiB\n",footprint());}
   if(maximum>=8) {STAGE(8,"mrt",mrt(device,q));printf("FUXI MTL: stage 8 footprint %llu KiB\n",footprint());}
   if(maximum>=9) {STAGE(9,"volume",volume(device,q));printf("FUXI MTL: stage 9 footprint %llu KiB\n",footprint());}
 if(maximum>=10) {STAGE(10,"mipchain",mipchain(device,q));printf("FUXI MTL: stage 10 footprint %llu KiB\n",footprint());}
   arm(10);[a release];[q release];
  }
  [device release];alarm(0);puts("FUXI MTL: done PASS");
 } @catch(NSException *e) {fail("exception %s %s",e.name.UTF8String,e.reason.UTF8String);} }
 return 0;
}
