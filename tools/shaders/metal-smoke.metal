// Original Fuxi M5b test. No Apple shader inputs or precompiled code.
#include <metal_stdlib>
using namespace metal;
vertex float4 fuxi_vertex(uint id [[vertex_id]]) {
    float x = id == 0 ? -0.5f : (id == 1 ? 0.5f : 0.0f);
    float y = id == 2 ? 0.5f : -0.5f;
    return float4(x, y, 0.0f, 1.0f);
}
fragment float4 fuxi_fragment() { return float4(1.0f, 0.0f, 0.0f, 1.0f); }

// Original CA-shaped control: two triangles cover the target; fragment pixel
// coordinates sample a separate BGRA8 texture with a runtime sampler at slot 0.
vertex float4 fuxi_quad(uint id [[vertex_id]]) {
    bool right = id == 1 || id == 4 || id == 5;
    bool top = id == 2 || id == 3 || id == 5;
    return float4(right ? 1.0f : -1.0f, top ? 1.0f : -1.0f, 0.0f, 1.0f);
}
fragment float4 fuxi_sample(float4 position [[position]],
                            texture2d<float> image [[texture(0)]],
                            sampler filtering [[sampler(0)]]) {
    return image.sample(filtering, position.xy / 256.0f);
}

// M7: original premultiplied layer renderer. CI's existing recipe compiles this
// file with Xcode metal; Linux AIR controls do not replace this qualification.
constant bool fuxi_m7_uniform [[function_constant(0)]];
struct FuxiM7Vertex {float2 position [[attribute(0)]];float2 uv [[attribute(1)]];float4 color [[attribute(2)]];};
struct FuxiM7Varying {float4 position [[position]];float2 uv;float4 color;};
vertex FuxiM7Varying fuxi_layer_vertex(FuxiM7Vertex v [[stage_in]],constant float4 &geometry [[buffer(2)]]) {
    return {float4(v.position,0.0f,1.0f)*geometry,v.uv,v.color};
}
fragment float4 fuxi_layer_fragment(FuxiM7Varying v [[stage_in]],
    texture2d<float> layer [[texture(0)]],sampler filtering [[sampler(0)]],
    constant float4 &tint [[buffer(1)]]) {
    return layer.sample(filtering,v.uv)*v.color*(fuxi_m7_uniform?tint:float4(1.0f,0.0f,0.0f,1.0f));
}
// M9 device coverage: multiple color targets (stage 8).
struct FuxiMrtOutput {float4 c0 [[color(0)]];float4 c1 [[color(1)]];float4 c2 [[color(2)]];};
fragment FuxiMrtOutput fuxi_mrt_fragment() {
    FuxiMrtOutput o;
    o.c0 = float4(1.0f, 0.0f, 0.0f, 1.0f);
    o.c1 = float4(0.0f, 0.5f, 0.0f, 1.0f);
    o.c2 = float4(0.0f, 0.0f, 1.0f, 1.0f);
    return o;
}
// M9 device coverage: sample a selected mip level view (stage 7) and a 3D
// texture slice (stage 9). Both take the normalized texel step in buffer 0/1.
fragment float4 fuxi_quad_sample(float4 position [[position]],
    texture2d<float> image [[texture(0)]],sampler filtering [[sampler(0)]],
    constant float2 &inverse [[buffer(0)]]) {
    return image.sample(filtering,position.xy*inverse);
}
fragment float4 fuxi_volume_sample(float4 position [[position]],
    texture3d<float> volume [[texture(0)]],sampler filtering [[sampler(0)]],
    constant float &slice [[buffer(0)]],constant float2 &inverse [[buffer(1)]]) {
    return volume.sample(filtering,float3(position.x*inverse.x,position.y*inverse.y,slice));
}
// Extra original CI control for the four small fragment slots.
fragment float4 fuxi_four_fragment(FuxiM7Varying v [[stage_in]],
 texture2d<float> a [[texture(0)]],texture2d<float> b [[texture(1)]],
 texture2d<float> c [[texture(2)]],texture2d<float> d [[texture(3)]],
 sampler s0 [[sampler(0)]],sampler s1 [[sampler(1)]],sampler s2 [[sampler(2)]],sampler s3 [[sampler(3)]]) {
 return (a.sample(s0,v.uv)+b.sample(s1,v.uv)+c.sample(s2,v.uv)+d.sample(s3,v.uv))*0.25f;
}
