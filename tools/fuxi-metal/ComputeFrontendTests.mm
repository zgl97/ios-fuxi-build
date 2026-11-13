/* Native Metal compute selectors/validation/ownership, with the portable CPU model. */
#define FUXI_MTL_FRONTEND_TEST
#define FUXI_MTL_BACKEND_HEADER "TestBackend.hpp"
#include "Frontend.mm"
#include <cassert>
#pragma clang diagnostic ignored "-Wdeprecated-declarations"
#pragma clang diagnostic ignored "-Wnonnull"
@interface FuxiComputeTestBase : NSObject {uint8_t bytes[520];}
@end
@implementation FuxiComputeTestBase
@end
static void refuses(FuxiCommandBuffer *c,void (^body)()){
 size_t n=c->_ops.size();body();assert(c->_rejected && c.error && c->_ops.size()==n);
 c->_rejected=NO;[c->_error release];c->_error=nil;
}
int main(){@autoreleasepool {
 Class cls=makeDeviceClass([FuxiComputeTestBase class]);id device=[cls alloc];FuxiOwner *state=[FuxiOwner new];state->backend=new fuxi::Backend;
 objc_setAssociatedObject(device,&ownerKey,state,OBJC_ASSOCIATION_RETAIN_NONATOMIC);[state release];fuxi::Backend::modelTextures=true;
 FuxiQueue *queue=(FuxiQueue *)[device newCommandQueue];
 FuxiLibrary *lib=[FuxiLibrary new];lib->_device=[device retain];
 FuxiFunction *kernel=[FuxiFunction new];kernel->_library=[lib retain];kernel->_name=[@"test_kernel" copy];kernel->_stage=3;
 NSError *error=nil;FuxiPipeline *pipeline=(FuxiPipeline *)[device newComputePipelineStateWithFunction:(id<MTLFunction>)kernel error:&error];
 assert(pipeline && !error && pipeline->_compute && pipeline.maxTotalThreadsPerThreadgroup==1024 && pipeline.threadExecutionWidth==32);
 assert([pipeline conformsToProtocol:@protocol(MTLComputePipelineState)]);
 FuxiFunction *vertex=[FuxiFunction new];vertex->_library=[lib retain];vertex->_name=[@"test_vertex" copy];vertex->_stage=1;
 error=nil;assert(![device newComputePipelineStateWithFunction:(id<MTLFunction>)vertex error:&error] && error);
 MTLComputePipelineDescriptor *pd=[MTLComputePipelineDescriptor new];pd.computeFunction=(id<MTLFunction>)kernel;
 error=nil;FuxiPipeline *descriptorPipeline=(FuxiPipeline *)[device newComputePipelineStateWithDescriptor:pd options:MTLPipelineOptionNone reflection:nil error:&error];
 assert(descriptorPipeline && !error);[descriptorPipeline release];[pd release];
 FuxiBuffer *buffer=(FuxiBuffer *)[device newBufferWithLength:256 options:0];assert(buffer);
 MTLTextureDescriptor *td=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatBGRA8Unorm width:8 height:8 mipmapped:NO];td.usage=MTLTextureUsageShaderRead|MTLTextureUsageShaderWrite;td.storageMode=MTLStorageModeShared;
 FuxiTexture *texture=(FuxiTexture *)[device newTextureWithDescriptor:td];assert(texture);
 FuxiCommandBuffer *c=(FuxiCommandBuffer *)[queue commandBuffer];id<MTLComputeCommandEncoder> e=(id)[c computeCommandEncoder];
 assert(e && [(id)e conformsToProtocol:@protocol(MTLComputeCommandEncoder)]);
 [e setComputePipelineState:(id)pipeline];[e setBuffer:(id)buffer offset:0 atIndex:0];[e setBytes:"abcd" length:4 atIndex:1];[e setTexture:(id)texture atIndex:0];
 [e dispatchThreadgroups:MTLSizeMake(2,3,4) threadsPerThreadgroup:MTLSizeMake(8,4,1)];
 assert(!c->_rejected && c->_ops.size()==1);const auto &op=c->_ops[0];assert(op.compute && op.computeGroups[0]==2 && op.computeGroups[1]==3 && op.computeGroups[2]==4 && op.computeThreads[0]==8 && op.computeThreads[1]==4 && op.computeThreads[2]==1);
 [e dispatchThreadgroups:MTLSizeMake(1,1,1) threadsPerThreadgroup:MTLSizeMake(8,4,1)];
 assert(!c->_rejected && c->_ops.size()==2 && c->_ops[1].compute && c->_ops[1].computeGroups[0]==1);
 [e endEncoding];refuses(c,^{[e dispatchThreadgroups:MTLSizeMake(1,1,1) threadsPerThreadgroup:MTLSizeMake(1,1,1)];});
 [c commit];assert(!c.error);
 c=(FuxiCommandBuffer *)[queue commandBuffer];e=(id)[c computeCommandEncoder];[e setComputePipelineState:(id)pipeline];
 MTLSize groups=MTLSizeMake((100+31)/32,(7+3)/4,(2+1)/2);[e dispatchThreads:MTLSizeMake(100,7,2) threadsPerThreadgroup:MTLSizeMake(32,4,2)];
 assert(c->_ops.size()==1 && c->_ops[0].computeGroups[0]==groups.width && c->_ops[0].computeGroups[1]==groups.height && c->_ops[0].computeGroups[2]==groups.depth);
 refuses(c,^{[e dispatchThreadgroups:MTLSizeMake(1,1,1) threadsPerThreadgroup:MTLSizeMake(64,32,1)];});
 [e endEncoding];[c commit];assert(!c.error);
 c=(FuxiCommandBuffer *)[queue commandBuffer];e=(id)[c computeCommandEncoder];
 refuses(c,^{[e dispatchThreadgroups:MTLSizeMake(1,1,1) threadsPerThreadgroup:MTLSizeMake(1,1,1)];});
 [e setComputePipelineState:(id)pipeline];
 refuses(c,^{[e dispatchThreadgroups:MTLSizeMake(1,1,1) threadsPerThreadgroup:MTLSizeMake(0,1,1)];});
 refuses(c,^{[e dispatchThreadgroupsWithIndirectBuffer:(id)buffer indirectBufferOffset:0 threadsPerThreadgroup:MTLSizeMake(1,1,1)];});
 [e endEncoding];[c commit];assert(!c.error);
 [texture release];[buffer release];[vertex release];[kernel release];[lib release];[pipeline release];[queue release];[device release];
 puts("PASS: M9n compute pipeline/encoder dispatch, threadgroups rounding and validation");
}}