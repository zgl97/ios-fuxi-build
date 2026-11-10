/* Real Objective-C ownership/validation around the portable encoding shortcuts. */
#define FUXI_MTL_FRONTEND_TEST
#define FUXI_MTL_BACKEND_HEADER "TestBackend.hpp"
#include "Frontend.mm"
#include "IOS16Defaults.hpp"
#include <cassert>
@interface FuxiEncodingBase : NSObject {uint8_t bytes[520];}
@end
@implementation FuxiEncodingBase
@end
int main(){@autoreleasepool{
 Class cls=makeDeviceClass([FuxiEncodingBase class]);id device=[cls alloc];FuxiOwner *state=[FuxiOwner new];state->backend=new fuxi::Backend;
 objc_setAssociatedObject(device,&ownerKey,state,OBJC_ASSOCIATION_RETAIN_NONATOMIC);[state release];fuxi::Backend::modelTextures=true;
 FuxiQueue *queue=[device newCommandQueue];FuxiPipeline *pipeline=[FuxiPipeline new];pipeline->_device=[device retain];pipeline->_pipeline=new fuxi::Pipeline{};
 MTLTextureDescriptor *td=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatBGRA8Unorm width:16 height:16 mipmapped:NO];td.storageMode=MTLStorageModePrivate;td.usage=MTLTextureUsageRenderTarget|MTLTextureUsageShaderRead;
 id target=[device newTextureWithDescriptor:td],sample=[device newTextureWithDescriptor:td];assert(target && sample);
 MTLSamplerDescriptor *sd=[MTLSamplerDescriptor new];id sampler=[device newSamplerStateWithDescriptor:sd];[sd release];
 MTLRenderPassDescriptor *pass=ios16PassDefaults();pass.colorAttachments[0].texture=target;pass.colorAttachments[0].loadAction=MTLLoadActionClear;pass.colorAttachments[0].storeAction=MTLStoreActionStore;
 __block unsigned released=0;uint8_t ring[4096]{};FuxiBuffer *buffer=(FuxiBuffer *)[device newBufferWithBytesNoCopy:ring length:sizeof(ring) options:MTLResourceStorageModeShared deallocator:^(void *,NSUInteger){++released;}];assert(buffer->_shadow);
 unsigned draws=0;fuxi::Backend::modelDraw=[&](const fuxi::Operation &op){assert(op.buffers[0][0].buffer==buffer->_buffer);++draws;};
 FuxiCommandBuffer *c=(FuxiCommandBuffer *)[queue commandBuffer];auto *storage=c->_ops.data();size_t retained=0;
 for(unsigned encoder=0;encoder<2;++encoder){FuxiRender *r=(FuxiRender *)[c renderCommandEncoderWithDescriptor:pass];assert(r);
  for(unsigned i=0;i<8;++i){[r setRenderPipelineState:(id)pipeline];[r setVertexBuffer:(id)buffer offset:i*32 atIndex:0];[r setFragmentBuffer:(id)buffer offset:0 atIndex:0];[r setFragmentTexture:sample atIndex:0];[r setFragmentSamplerState:sampler atIndex:0];[r drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3];}
  if(!encoder)retained=c->_resources.count;assert(retained==5 && c->_resources.count==retained && c->_resourceSet.size()==retained);
  [r useResource:(id)buffer usage:MTLResourceUsageRead stages:MTLRenderStageVertex];assert(c->_shadowUses.at(buffer->_buffer).ranges.fullReason(fuxi::ShadowFullReason::useResource));
  retainCommandResource(c,buffer,false,fuxi::ShadowFullReason::blit);assert(c->_shadowUses.at(buffer->_buffer).ranges.fullReason(fuxi::ShadowFullReason::blit));
  [r endEncoding];size_t ops=c->_ops.size();
  for(unsigned mode=0;mode<3;++mode){if(mode==0)[r setRenderPipelineState:(id)pipeline];else if(mode==1)[r setFragmentTexture:sample atIndex:0];else [r setFragmentSamplerState:sampler atIndex:0];
   assert(c->_rejected && c.error && c->_ops.size()==ops);c->_rejected=NO;[c->_error release];c->_error=nil;}
 }
 // Command ownership alone keeps vertex data alive; completion releases it once.
 [buffer release];assert(!released);[c commit];assert(!c.error && draws==16 && released==1 && !c->_resources.count && c->_resourceSet.empty());
 FuxiCommandBuffer *inlineCommand=(FuxiCommandBuffer *)[queue commandBuffer];assert(inlineCommand->_ops.data()==storage);
 FuxiRender *inlineRender=(FuxiRender *)[inlineCommand renderCommandEncoderWithDescriptor:pass];[inlineRender setRenderPipelineState:(id)pipeline];
 uint8_t firstBytes[37],nextBytes[128];memset(firstBytes,0x43,sizeof(firstBytes));memset(nextBytes,0x65,sizeof(nextBytes));
 [inlineRender setVertexBytes:firstBytes length:sizeof(firstBytes) atIndex:0];[inlineRender drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3];
 memset(firstBytes,0xff,sizeof(firstBytes));[inlineRender setVertexBytes:nextBytes length:sizeof(nextBytes) atIndex:0];[inlineRender drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3];[inlineRender endEncoding];
 auto firstBinding=inlineCommand->_ops[0].buffers[0][0],nextBinding=inlineCommand->_ops[1].buffers[0][0];
 assert(firstBinding.buffer->upload && nextBinding.buffer->upload && firstBinding.buffer!=nextBinding.buffer && firstBinding.offset==0 && nextBinding.offset==0 && firstBinding.length()==37 && nextBinding.length()==128);
 assert(((uint8_t *)firstBinding.buffer->contents)[0]==0x43 && !((uint8_t *)firstBinding.buffer->contents)[37] && ((uint8_t *)nextBinding.buffer->contents)[0]==0x65 && !((uint8_t *)nextBinding.buffer->contents)[128]);
 auto uploadBuffer=firstBinding.buffer;unsigned inlineDraws=0;fuxi::Backend::modelDraw=[&](const fuxi::Operation &op){auto b=op.buffers[0][0];assert(b.bytes && b.length()-b.offset==(inlineDraws?128:37));++inlineDraws;};
 [inlineCommand commit];assert(!inlineCommand.error && inlineDraws==2);
 FuxiCommandBuffer *reused=(FuxiCommandBuffer *)[queue commandBuffer];FuxiRender *reusedRender=(FuxiRender *)[reused renderCommandEncoderWithDescriptor:pass];
 [reusedRender setVertexBytes:nextBytes length:sizeof(nextBytes) atIndex:0];auto reusedBinding=reusedRender->_state.buffers[0][0];assert(reusedBinding.buffer && reusedBinding.buffer->upload && reusedBinding.bytes && reusedBinding.offset==0 && ((const uint8_t *)reusedBinding.buffer->contents)[0]==0x65);[reusedRender endEncoding];[reused commit];assert(!reused.error);
 fuxi::Backend::modelDraw={};[pipeline release];[sampler release];[sample release];[target release];[queue release];[device release];
 }assert(fuxi::Backend::live==0);puts("PASS: exact command ownership/release, encoder rebinds, reason escalation and redundant-state rejection after endEncoding");}
