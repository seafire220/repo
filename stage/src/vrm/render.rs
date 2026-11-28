//! Draws a posed VRM avatar with OpenGL ES 3.0: skinned on the graphics chip (each skin's joint
//! matrices sent as a small texture each frame, so avatars with hundreds of bones work on chips that
//! allow few uniforms), face shapes mixed on the processor only for the meshes whose shapes changed,
//! and MToon's toon look: lit and shade colours split at a soft line, a rim of light, emission, and
//! inverted-hull outlines. Textures are cut down to the memory profile's size as they are uploaded,
//! and the processor's copies are dropped once the graphics chip has them.

use std::collections::HashMap;

use glam::{Mat4, Vec3};
use glow::HasContext;

use super::model::{AlphaMode, ColorKind, OutlineMode, TexRef};
use super::VrmPuppet;
use crate::live2d::compile;

const VERTEX: &str = r#"
layout(location = 0) in vec3 a_pos;
layout(location = 1) in vec3 a_normal;
layout(location = 2) in vec2 a_uv;
layout(location = 3) in uvec4 a_joints;
layout(location = 4) in vec4 a_weights;
uniform highp sampler2D u_joint_tex;
uniform sampler2D u_outline_tex;
uniform int u_skinned;
uniform mat4 u_model;
uniform mat4 u_view_proj;
uniform vec3 u_camera;
uniform float u_outline;
uniform int u_outline_screen;
uniform int u_has_outline_tex;
uniform vec2 u_uv_scale;
uniform vec2 u_uv_offset;
out vec3 v_normal;
out vec2 v_uv;
out vec3 v_to_camera;
mat4 joint(uint j) {
	int x = int(j) * 3;
	vec4 r0 = texelFetch(u_joint_tex, ivec2(x, 0), 0);
	vec4 r1 = texelFetch(u_joint_tex, ivec2(x + 1, 0), 0);
	vec4 r2 = texelFetch(u_joint_tex, ivec2(x + 2, 0), 0);
	return transpose(mat4(r0, r1, r2, vec4(0.0, 0.0, 0.0, 1.0)));
}
void main() {
	mat4 m = u_model;
	if (u_skinned == 1) {
		m = joint(a_joints.x) * a_weights.x + joint(a_joints.y) * a_weights.y
		  + joint(a_joints.z) * a_weights.z + joint(a_joints.w) * a_weights.w;
	}
	vec4 world = m * vec4(a_pos, 1.0);
	vec3 n = normalize(mat3(m) * a_normal);
	vec2 uv = a_uv * u_uv_scale + u_uv_offset;
	if (u_outline > 0.0) {
		float width = u_outline;
		if (u_has_outline_tex == 1) {
			width *= textureLod(u_outline_tex, uv, 0.0).g;
		}
		if (u_outline_screen == 1) {
			width *= distance(world.xyz, u_camera);
		}
		world.xyz += n * width;
	}
	v_normal = n;
	v_uv = uv;
	v_to_camera = u_camera - world.xyz;
	gl_Position = u_view_proj * world;
}
"#;

const FRAGMENT: &str = r#"
precision mediump float;
in vec3 v_normal;
in vec2 v_uv;
in vec3 v_to_camera;
uniform sampler2D u_base_tex;
uniform sampler2D u_shade_tex;
uniform sampler2D u_emissive_tex;
uniform vec4 u_base;
uniform vec3 u_shade;
uniform vec3 u_emissive;
uniform vec3 u_rim;
uniform float u_rim_power;
uniform float u_rim_lift;
uniform float u_shift;
uniform float u_toony;
uniform int u_alpha_mode;
uniform float u_cutoff;
uniform int u_unlit;
uniform int u_shade_from_base;
uniform vec3 u_light_dir;
uniform float u_ambient;
uniform int u_outline_pass;
uniform vec3 u_outline_color;
out vec4 frag;
void main() {
	vec4 base = texture(u_base_tex, v_uv) * u_base;
	if (u_alpha_mode == 1 && base.a < u_cutoff) {
		discard;
	}
	if (u_outline_pass == 1) {
		frag = vec4(u_outline_color, u_alpha_mode == 2 ? base.a : 1.0);
		return;
	}
	vec3 colour = base.rgb;
	if (u_unlit == 0) {
		vec3 n = normalize(v_normal);
		if (!gl_FrontFacing) {
			n = -n;
		}
		float lit = dot(n, u_light_dir) + u_shift;
		lit = clamp((lit + 1.0 - u_toony) / max(2.0 - 2.0 * u_toony, 0.0001), 0.0, 1.0);
		vec3 shade_tex = u_shade_from_base == 1 ? texture(u_base_tex, v_uv).rgb : texture(u_shade_tex, v_uv).rgb;
		vec3 shade = u_shade * shade_tex;
		colour = mix(shade, base.rgb, lit);
		colour += base.rgb * u_ambient;
		float facing = clamp(dot(n, normalize(v_to_camera)), 0.0, 1.0);
		colour += u_rim * pow(clamp(1.0 - facing + u_rim_lift, 0.0, 1.0), max(u_rim_power, 0.001));
	}
	colour += u_emissive * texture(u_emissive_tex, v_uv).rgb;
	frag = vec4(min(colour, vec3(1.0)), u_alpha_mode == 2 ? base.a : 1.0);
}
"#;

struct Program {
	program: glow::Program,
	loc: std::collections::HashMap<&'static str, glow::UniformLocation>,
}

const UNIFORMS: [&str; 32] = [
	"u_joint_tex", "u_outline_tex", "u_skinned", "u_model", "u_view_proj", "u_camera", "u_outline", "u_outline_screen",
	"u_has_outline_tex", "u_uv_scale", "u_uv_offset", "u_base_tex", "u_shade_tex", "u_emissive_tex", "u_base", "u_shade",
	"u_emissive", "u_rim", "u_rim_power", "u_rim_lift", "u_shift", "u_toony", "u_alpha_mode", "u_cutoff", "u_unlit",
	"u_shade_from_base", "u_light_dir", "u_ambient", "u_outline_pass", "u_outline_color", "u_outline_tex", "u_outline",
];

impl Program {
	unsafe fn new(gl: &glow::Context) -> Result<Program, String> {
		let program = compile(gl, VERTEX, FRAGMENT)?;
		let mut loc = std::collections::HashMap::new();
		for name in UNIFORMS {
			if let Some(l) = gl.get_uniform_location(program, name) {
				loc.insert(name, l);
			}
		}
		Ok(Program { program, loc })
	}

	fn at(&self, name: &'static str) -> Option<&glow::UniformLocation> {
		self.loc.get(name)
	}
}

/// A mesh's set of points on the graphics chip, shared by the parts that use it.
struct PointsGl {
	mesh: usize,
	set: usize,
	positions: glow::Buffer,
	normals: glow::Buffer,
	uvs: glow::Buffer,
	/// Bone numbers and weights, if the points have them.
	skinning: Option<(glow::Buffer, glow::Buffer)>,
	/// The points as modelled, kept only for sets whose face shapes change.
	base: Option<Vec<Vec3>>,
}

/// One primitive on the graphics chip: its own triangles, over a shared set of points.
struct Part {
	vao: glow::VertexArray,
	indices: glow::Buffer,
	count: i32,
	material: Option<usize>,
	skin: Option<usize>,
	node: usize,
}

/// How much of the avatar the screen shows.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Shot {
	/// Head and shoulders.
	Bust,
	/// Head to mid-thigh (the default: the face is large and the arms' gestures show).
	Upper,
	/// Head to toe.
	Full,
}

impl Shot {
	pub fn parse(s: &str) -> Option<Shot> {
		match s {
			"bust" => Some(Shot::Bust),
			"upper" => Some(Shot::Upper),
			"full" => Some(Shot::Full),
			_ => None,
		}
	}
}

pub struct VrmRenderer {
	gl: glow::Context,
	program: Program,
	textures: Vec<Option<glow::Texture>>,
	white: glow::Texture,
	joint_textures: Vec<glow::Texture>,
	identity_joints: glow::Texture,
	parts: Vec<Part>,
	points: Vec<PointsGl>,
	width: u32,
	height: u32,
	pub background: [f32; 3],
	pub shot: Shot,
	/// How far the camera has pulled back for raised arms (1 = not at all), eased.
	zoom: f32,
	/// How far the view has grown upwards for hands raised above the head, in metres (eased).
	lift: f32,
	/// Bytes of texture uploaded, for the memory budget, and the largest side they were kept at.
	pub texture_bytes: usize,
	pub texture_side: u32,
	/// Outlines drawn (left out when the drawing is kept light).
	pub outlines: bool,
	/// Drawn at this fraction of the screen's resolution and scaled up (1: full), for a slow device.
	scale: f32,
	offscreen: Option<Offscreen>,
	/// Bits of depth the screen has (24 wanted; 0 would draw insides over faces).
	pub depth_bits: i32,
}

/// Texture memory an avatar may use, by profile: what fits beside everything else in the stage's
/// share of a 1 GB board (L1) or a larger one (L2).
const TEXTURE_BUDGET_L1: usize = 96 << 20;
const TEXTURE_BUDGET_L2: usize = 160 << 20;

/// A framebuffer drawn into at a lower resolution, then scaled up to the screen.
struct Offscreen {
	fbo: glow::Framebuffer,
	color: glow::Renderbuffer,
	depth: glow::Renderbuffer,
	width: u32,
	height: u32,
}

unsafe fn upload_rgba(gl: &glow::Context, w: u32, h: u32, data: &[u8], mipmaps: bool) -> Result<glow::Texture, String> {
	let tex = gl.create_texture()?;
	gl.bind_texture(glow::TEXTURE_2D, Some(tex));
	gl.pixel_store_i32(glow::UNPACK_ALIGNMENT, 1);
	gl.tex_image_2d(glow::TEXTURE_2D, 0, glow::RGBA8 as i32, w as i32, h as i32, 0, glow::RGBA, glow::UNSIGNED_BYTE, Some(data));
	if mipmaps {
		gl.generate_mipmap(glow::TEXTURE_2D);
	}
	let min = if mipmaps { glow::LINEAR_MIPMAP_LINEAR } else { glow::LINEAR };
	gl.tex_parameter_i32(glow::TEXTURE_2D, glow::TEXTURE_MIN_FILTER, min as i32);
	gl.tex_parameter_i32(glow::TEXTURE_2D, glow::TEXTURE_MAG_FILTER, glow::LINEAR as i32);
	gl.tex_parameter_i32(glow::TEXTURE_2D, glow::TEXTURE_WRAP_S, glow::REPEAT as i32);
	gl.tex_parameter_i32(glow::TEXTURE_2D, glow::TEXTURE_WRAP_T, glow::REPEAT as i32);
	Ok(tex)
}

unsafe fn joint_texture(gl: &glow::Context, joints: usize) -> Result<glow::Texture, String> {
	let tex = gl.create_texture()?;
	gl.bind_texture(glow::TEXTURE_2D, Some(tex));
	let zero = vec![0f32; joints.max(1) * 3 * 4];
	gl.tex_image_2d(
		glow::TEXTURE_2D,
		0,
		glow::RGBA32F as i32,
		(joints.max(1) * 3) as i32,
		1,
		0,
		glow::RGBA,
		glow::FLOAT,
		Some(bytes_of(&zero)),
	);
	for (k, v) in [(glow::TEXTURE_MIN_FILTER, glow::NEAREST), (glow::TEXTURE_MAG_FILTER, glow::NEAREST)] {
		gl.tex_parameter_i32(glow::TEXTURE_2D, k, v as i32);
	}
	Ok(tex)
}

fn bytes_of<T: Copy>(data: &[T]) -> &[u8] {
	// Plain numbers only (f32, u16, u32): no padding, any byte pattern is valid to read.
	unsafe { std::slice::from_raw_parts(data.as_ptr() as *const u8, std::mem::size_of_val(data)) }
}

/// The first three rows of each matrix, one after another, for the joint texture.
fn rows(matrices: &[Mat4]) -> Vec<f32> {
	let mut out = Vec::with_capacity(matrices.len() * 12);
	for m in matrices {
		let t = m.transpose();
		out.extend_from_slice(&t.x_axis.to_array());
		out.extend_from_slice(&t.y_axis.to_array());
		out.extend_from_slice(&t.z_axis.to_array());
	}
	out
}

impl VrmRenderer {
	pub fn new(gl: glow::Context, puppet: &mut VrmPuppet) -> Result<VrmRenderer, String> {
		unsafe {
			let program = Program::new(&gl)?;
			let white = upload_rgba(&gl, 1, 1, &[255, 255, 255, 255], false)?;
			let glb = puppet.take_file();
			let model = &puppet.model;
			// Only the textures the drawing uses, at most the profile's size.
			let mut wanted = vec![false; model.texture_images.len()];
			let mut mark = |t: &Option<TexRef>| {
				if let Some(t) = t {
					if let Some(w) = wanted.get_mut(t.texture) {
						*w = true;
					}
				}
			};
			for m in &model.materials {
				mark(&m.base_texture);
				mark(&m.emissive_texture);
				if m.shade_texture.map(|t| t.texture) != m.base_texture.map(|t| t.texture) && puppet.profile == super::Profile::L2 {
					mark(&m.shade_texture);
				}
			}
			let max_side = (gl.get_parameter_i32(glow::MAX_TEXTURE_SIZE) as u32).max(512);
			let mut side = puppet.texture_side.min(max_side);
			let mut textures = vec![None; wanted.len()];
			let mut texture_bytes = 0;
			let mut warnings = Vec::new();
			// A guard on memory: if the textures would not fit the budget at this size, halve it until they do.
			if let Some(glb) = &glb {
				let sizes: Vec<(u32, u32)> = wanted
					.iter()
					.enumerate()
					.filter(|(_, w)| **w)
					.filter_map(|(t, _)| model.texture_images.get(t).copied().flatten())
					.filter_map(|image| image_size(glb, image))
					.collect();
				let budget = if puppet.profile == super::Profile::L2 { TEXTURE_BUDGET_L2 } else { TEXTURE_BUDGET_L1 };
				let bytes_at = |s: u32| -> usize {
					sizes
						.iter()
						.map(|&(w, h)| {
							let k = (s as f32 / w.max(h) as f32).min(1.0);
							((w as f32 * k) as usize) * ((h as f32 * k) as usize) * 4 * 4 / 3
						})
						.sum()
				};
				let wanted_side = side;
				while side > 256 && bytes_at(side) > budget {
					side /= 2;
				}
				if side < wanted_side {
					warnings.push(format!(
						"the avatar's textures were drawn at {side} pixels a side, not {wanted_side}, to fit this device's memory"
					));
				}
			}
			if let Some(glb) = &glb {
				for (t, want) in wanted.iter().enumerate() {
					let Some(image) = model.texture_images.get(t).copied().flatten().filter(|_| *want) else { continue };
					match decode(glb, image, side.min(max_side)) {
						Ok((w, h, rgba)) => {
							texture_bytes += rgba.len() * 4 / 3;
							textures[t] = Some(upload_rgba(&gl, w, h, &rgba, true)?);
						}
						Err(e) => warnings.push(format!("texture {t} was not drawn: {e}")),
					}
				}
			}
			drop(glb);

			let joint_textures = model.skins.iter().map(|s| joint_texture(&gl, s.joints.len())).collect::<Result<Vec<_>, _>>()?;
			let identity_joints = joint_texture(&gl, 1)?;
			let mut parts = Vec::new();
			let mut points: Vec<PointsGl> = Vec::new();
			let mut uploaded: HashMap<(usize, usize), usize> = HashMap::new();
			for (n, node) in model.nodes.iter().enumerate() {
				let Some((mi, mesh)) = node.mesh.and_then(|m| model.meshes.get(m).map(|x| (m, x))) else { continue };
				for p in &mesh.primitives {
					if p.indices.is_empty() {
						continue;
					}
					// Each set of points goes to the graphics chip once, however many parts use it.
					let set = match uploaded.get(&(mi, p.points)) {
						Some(&k) => k,
						None => {
							let src = &mesh.points[p.points];
							let morphed = !src.targets.is_empty();
							let upload = |data: &[f32], usage: u32| -> Result<glow::Buffer, String> {
								let b = gl.create_buffer()?;
								gl.bind_buffer(glow::ARRAY_BUFFER, Some(b));
								gl.buffer_data_u8_slice(glow::ARRAY_BUFFER, bytes_of(data), usage);
								Ok(b)
							};
							let flat: Vec<f32> = src.positions.iter().flat_map(|v| v.to_array()).collect();
							let positions = upload(&flat, if morphed { glow::DYNAMIC_DRAW } else { glow::STATIC_DRAW })?;
							let normals = upload(&src.normals.iter().flat_map(|v| v.to_array()).collect::<Vec<f32>>(), glow::STATIC_DRAW)?;
							let uvs = upload(&src.uvs.iter().flat_map(|v| v.to_array()).collect::<Vec<f32>>(), glow::STATIC_DRAW)?;
							let skinning = if src.joints.is_empty() {
								None
							} else {
								let joints: Vec<u16> = src.joints.iter().flat_map(|j| *j).collect();
								let jb = gl.create_buffer()?;
								gl.bind_buffer(glow::ARRAY_BUFFER, Some(jb));
								gl.buffer_data_u8_slice(glow::ARRAY_BUFFER, bytes_of(&joints), glow::STATIC_DRAW);
								let wb = upload(&src.weights.iter().flat_map(|w| *w).collect::<Vec<f32>>(), glow::STATIC_DRAW)?;
								Some((jb, wb))
							};
							points.push(PointsGl {
								mesh: mi,
								set: p.points,
								positions,
								normals,
								uvs,
								skinning,
								base: if morphed { Some(src.positions.clone()) } else { None },
							});
							uploaded.insert((mi, p.points), points.len() - 1);
							points.len() - 1
						}
					};
					let gp = &points[set];
					let vao = gl.create_vertex_array()?;
					gl.bind_vertex_array(Some(vao));
					gl.bind_buffer(glow::ARRAY_BUFFER, Some(gp.positions));
					gl.enable_vertex_attrib_array(0);
					gl.vertex_attrib_pointer_f32(0, 3, glow::FLOAT, false, 12, 0);
					gl.bind_buffer(glow::ARRAY_BUFFER, Some(gp.normals));
					gl.enable_vertex_attrib_array(1);
					gl.vertex_attrib_pointer_f32(1, 3, glow::FLOAT, false, 12, 0);
					gl.bind_buffer(glow::ARRAY_BUFFER, Some(gp.uvs));
					gl.enable_vertex_attrib_array(2);
					gl.vertex_attrib_pointer_f32(2, 2, glow::FLOAT, false, 8, 0);
					let skin = node.skin.filter(|s| *s < model.skins.len()).filter(|_| gp.skinning.is_some());
					match (skin, gp.skinning) {
						(Some(_), Some((jb, wb))) => {
							gl.bind_buffer(glow::ARRAY_BUFFER, Some(jb));
							gl.enable_vertex_attrib_array(3);
							gl.vertex_attrib_pointer_i32(3, 4, glow::UNSIGNED_SHORT, 8, 0);
							gl.bind_buffer(glow::ARRAY_BUFFER, Some(wb));
							gl.enable_vertex_attrib_array(4);
							gl.vertex_attrib_pointer_f32(4, 4, glow::FLOAT, false, 16, 0);
						}
						_ => {
							// Not skinned: the shader uses the node's matrix and never reads these.
							gl.disable_vertex_attrib_array(3);
							gl.disable_vertex_attrib_array(4);
						}
					}
					let indices = gl.create_buffer()?;
					gl.bind_buffer(glow::ELEMENT_ARRAY_BUFFER, Some(indices));
					gl.buffer_data_u8_slice(glow::ELEMENT_ARRAY_BUFFER, bytes_of(&p.indices), glow::STATIC_DRAW);
					gl.bind_vertex_array(None);
					parts.push(Part { vao, indices, count: p.indices.len() as i32, material: p.material, skin, node: n });
				}
			}
			puppet.warnings.extend(warnings);
			// The processor keeps only what changes: points of shape-changing parts, and the shapes.
			release_static_data(puppet);
			let width = 720;
			let height = 1280;
			let depth_bits = gl.get_parameter_i32(0x0D56); // GL_DEPTH_BITS
			Ok(VrmRenderer {
				gl,
				program,
				textures,
				white,
				joint_textures,
				identity_joints,
				parts,
				points,
				width,
				height,
				background: [0.06, 0.06, 0.08],
				shot: Shot::Upper,
				zoom: 1.0,
				lift: 0.0,
				texture_bytes,
				texture_side: side,
				outlines: true,
				scale: 1.0,
				offscreen: None,
				depth_bits,
			})
		}
	}

	pub fn resize(&mut self, width: u32, height: u32) {
		self.width = width.max(1);
		self.height = height.max(1);
		unsafe { self.gl.viewport(0, 0, self.width as i32, self.height as i32) };
	}

	/// The camera: looking at the avatar from in front, framed for the shot, pulled back a little
	/// while the arms are raised so a waving hand stays on screen.
	fn camera(&mut self, puppet: &VrmPuppet) -> (Mat4, Vec3) {
		let f = puppet.framing;
		let aspect = self.width as f32 / self.height as f32;
		let fov = 20f32.to_radians();
		let top = f.top + (f.top - f.hips) * 0.06;
		let bottom = match self.shot {
			Shot::Bust => f.chest - (f.neck - f.chest) * 0.8,
			Shot::Upper => f.hips - (f.hips - f.knees) * 0.55,
			Shot::Full => f.feet,
		};
		let mut half_h = (top - bottom) * 0.5;
		// Wide enough for the shoulders and the arms hanging beside them (hair, wings and other
		// accessories may reach past the edges); wider while hands are raised out to the side.
		let room = match self.shot {
			Shot::Bust => 0.05,
			Shot::Upper => 0.12,
			Shot::Full => 0.16,
		};
		let mut reach: f32 = f.half_width + room;
		// And taller while hands are raised above the head (a stretch).
		let mut high = top;
		for hand in ["leftHand", "rightHand", "leftLowerArm", "rightLowerArm"] {
			if let Some(n) = puppet.model.bone(hand) {
				let p = puppet.rig.world_position(n);
				if p.y > bottom {
					reach = reach.max(p.x.abs() + 0.08);
				}
				high = high.max(p.y + 0.12);
			}
		}
		// At most a third further back (a waving hand may then reach the edge), and gently.
		let taller = (high - bottom) / (top - bottom).max(0.1);
		let needed = ((reach / aspect).max(half_h) / half_h).max(taller).clamp(1.0, 1.3);
		self.zoom += (needed - self.zoom) * 0.04;
		// The head keeps its place on the screen unless hands rise above it; then the view grows upwards.
		let lift = (high - top).max(0.0).min((self.zoom - 1.0) * 2.0 * half_h);
		self.lift += (lift - self.lift) * 0.04;
		half_h *= self.zoom;
		let centre = Vec3::new(0.0, top + self.lift - half_h, 0.0);
		let distance = half_h / (fov * 0.5).tan();
		let eye = centre + Vec3::new(0.0, 0.0, distance);
		let view = Mat4::look_at_rh(eye, centre, Vec3::Y);
		let proj = Mat4::perspective_rh_gl(fov, aspect, (distance * 0.1).max(0.01), distance * 4.0 + 10.0);
		(proj * view, eye)
	}

	/// Draw at this fraction of the screen's resolution (1: full), scaled up to the screen.
	pub fn set_scale(&mut self, scale: f32) {
		self.scale = scale.clamp(0.25, 1.0);
	}

	pub fn scale(&self) -> f32 {
		self.scale
	}

	/// The framebuffer to draw into: an offscreen one at the lower resolution, made (or remade) when
	/// needed; None for the screen itself.
	unsafe fn target(&mut self) -> Result<Option<(glow::Framebuffer, u32, u32)>, String> {
		if self.scale >= 0.999 {
			return Ok(None);
		}
		let (w, h) = (((self.width as f32 * self.scale) as u32).max(1), ((self.height as f32 * self.scale) as u32).max(1));
		if self.offscreen.as_ref().map(|o| (o.width, o.height)) != Some((w, h)) {
			let gl = &self.gl;
			if let Some(old) = self.offscreen.take() {
				gl.delete_framebuffer(old.fbo);
				gl.delete_renderbuffer(old.color);
				gl.delete_renderbuffer(old.depth);
			}
			let fbo = gl.create_framebuffer()?;
			let color = gl.create_renderbuffer()?;
			let depth = gl.create_renderbuffer()?;
			gl.bind_renderbuffer(glow::RENDERBUFFER, Some(color));
			gl.renderbuffer_storage(glow::RENDERBUFFER, glow::RGBA8, w as i32, h as i32);
			gl.bind_renderbuffer(glow::RENDERBUFFER, Some(depth));
			gl.renderbuffer_storage(glow::RENDERBUFFER, glow::DEPTH_COMPONENT24, w as i32, h as i32);
			gl.bind_framebuffer(glow::FRAMEBUFFER, Some(fbo));
			gl.framebuffer_renderbuffer(glow::FRAMEBUFFER, glow::COLOR_ATTACHMENT0, glow::RENDERBUFFER, Some(color));
			gl.framebuffer_renderbuffer(glow::FRAMEBUFFER, glow::DEPTH_ATTACHMENT, glow::RENDERBUFFER, Some(depth));
			let complete = gl.check_framebuffer_status(glow::FRAMEBUFFER) == glow::FRAMEBUFFER_COMPLETE;
			gl.bind_framebuffer(glow::FRAMEBUFFER, None);
			if !complete {
				gl.delete_framebuffer(fbo);
				gl.delete_renderbuffer(color);
				gl.delete_renderbuffer(depth);
				self.scale = 1.0;
				return Ok(None);
			}
			self.offscreen = Some(Offscreen { fbo, color, depth, width: w, height: h });
		}
		Ok(self.offscreen.as_ref().map(|o| (o.fbo, o.width, o.height)))
	}

	pub fn draw(&mut self, puppet: &mut VrmPuppet, background: Option<&crate::background::Background>) -> Result<(), String> {
		let (view_proj, eye) = self.camera(puppet);
		let target = unsafe { self.target()? };
		let gl = &self.gl;
		let model = &puppet.model;
		unsafe {
			let (vw, vh) = match target {
				Some((fbo, w, h)) => {
					gl.bind_framebuffer(glow::FRAMEBUFFER, Some(fbo));
					(w, h)
				}
				None => (self.width, self.height),
			};
			gl.viewport(0, 0, vw as i32, vh as i32);
			let [r, g, b] = self.background;
			gl.clear_color(r, g, b, 1.0);
			gl.depth_mask(true);
			gl.clear(glow::COLOR_BUFFER_BIT | glow::DEPTH_BUFFER_BIT);
			// The background goes into the same framebuffer (a smaller one, when drawn small), first.
			if let Some(picture) = background {
				picture.draw(gl, vw, vh);
			}
			gl.enable(glow::DEPTH_TEST);
			gl.depth_func(glow::LEQUAL);
			gl.disable(glow::STENCIL_TEST);
			gl.disable(glow::SCISSOR_TEST);

			// Face shapes: mix them on the processor, once per set of points, for the meshes whose weights changed.
			for set in &self.points {
				let Some(base) = &set.base else { continue };
				if !puppet.morph_dirty.get(set.mesh).copied().unwrap_or(false) {
					continue;
				}
				let weights = &puppet.morphs[set.mesh];
				let source = &model.meshes[set.mesh].points[set.set];
				let mut points = base.clone();
				for (t, target) in source.targets.iter().enumerate() {
					let w = weights.get(t).copied().unwrap_or(0.0);
					if w <= 0.0 {
						continue;
					}
					for (&i, d) in target.indices.iter().zip(&target.deltas) {
						if let Some(p) = points.get_mut(i as usize) {
							*p += *d * w;
						}
					}
				}
				let flat: Vec<f32> = points.iter().flat_map(|v| v.to_array()).collect();
				gl.bind_buffer(glow::ARRAY_BUFFER, Some(set.positions));
				gl.buffer_sub_data_u8_slice(glow::ARRAY_BUFFER, 0, bytes_of(&flat));
			}
			for d in puppet.morph_dirty.iter_mut() {
				*d = false;
			}
			// Joint matrices.
			for (s, tex) in self.joint_textures.iter().enumerate() {
				let data = rows(&puppet.rig.skins[s]);
				gl.bind_texture(glow::TEXTURE_2D, Some(*tex));
				gl.tex_sub_image_2d(
					glow::TEXTURE_2D,
					0,
					0,
					0,
					(data.len() / 4) as i32,
					1,
					glow::RGBA,
					glow::FLOAT,
					glow::PixelUnpackData::Slice(bytes_of(&data)),
				);
			}

			let p = &self.program;
			gl.use_program(Some(p.program));
			gl.uniform_matrix_4_f32_slice(p.at("u_view_proj"), false, &view_proj.to_cols_array());
			gl.uniform_3_f32(p.at("u_camera"), eye.x, eye.y, eye.z);
			let light = Vec3::new(-0.25, 0.45, 1.0).normalize();
			gl.uniform_3_f32(p.at("u_light_dir"), light.x, light.y, light.z);
			gl.uniform_1_f32(p.at("u_ambient"), 0.04);
			gl.uniform_1_i32(p.at("u_joint_tex"), 0);
			gl.uniform_1_i32(p.at("u_base_tex"), 1);
			gl.uniform_1_i32(p.at("u_shade_tex"), 2);
			gl.uniform_1_i32(p.at("u_emissive_tex"), 3);
			gl.uniform_1_i32(p.at("u_outline_tex"), 4);

			// Opaque and cut-out parts first, then outlines, then see-through parts in their order.
			let queue = |part: &Part| -> (u8, i32) {
				match part.material.and_then(|m| model.materials.get(m)) {
					Some(m) if m.alpha == AlphaMode::Blend => (2, m.queue),
					Some(m) => (if matches!(m.alpha, AlphaMode::Mask(_)) { 1 } else { 0 }, m.queue),
					None => (0, 0),
				}
			};
			let mut order: Vec<usize> = (0..self.parts.len()).collect();
			order.sort_by_key(|&i| queue(&self.parts[i]));
			let (solid, clear): (Vec<usize>, Vec<usize>) = order.into_iter().partition(|&i| queue(&self.parts[i]).0 < 2);
			for &i in &solid {
				self.draw_part(puppet, &self.parts[i], false);
			}
			if self.outlines {
				for &i in &solid {
					self.draw_part(puppet, &self.parts[i], true);
				}
			}
			for &i in &clear {
				self.draw_part(puppet, &self.parts[i], false);
				if self.outlines {
					self.draw_part(puppet, &self.parts[i], true);
				}
			}
			gl.disable(glow::BLEND);
			gl.disable(glow::CULL_FACE);
			gl.depth_mask(true);
			gl.bind_vertex_array(None);
			// Drawn small: scale it up to the screen.
			if let Some((fbo, w, h)) = target {
				gl.bind_framebuffer(glow::READ_FRAMEBUFFER, Some(fbo));
				gl.bind_framebuffer(glow::DRAW_FRAMEBUFFER, None);
				gl.blit_framebuffer(0, 0, w as i32, h as i32, 0, 0, self.width as i32, self.height as i32, glow::COLOR_BUFFER_BIT, glow::LINEAR);
				gl.bind_framebuffer(glow::FRAMEBUFFER, None);
			}
		}
		Ok(())
	}

	unsafe fn draw_part(&self, puppet: &VrmPuppet, part: &Part, outline: bool) {
		let gl = &self.gl;
		let p = &self.program;
		let model = &puppet.model;
		let default = super::model::Material::default();
		let mi = part.material.filter(|m| *m < model.materials.len());
		let m = mi.map(|i| &model.materials[i]).unwrap_or(&default);
		if outline && (m.outline_mode == OutlineMode::None || m.outline_width <= 0.0) {
			return;
		}
		let colors = mi.map(|i| puppet.material_colors[i]).unwrap_or([m.base, [0.7, 0.7, 0.7, 1.0], [0.0; 4], [0.0; 4], [0.0; 4]]);
		let (uv_scale, uv_offset) = mi.map(|i| puppet.material_uv[i]).unwrap_or((glam::Vec2::ONE, glam::Vec2::ZERO));
		let tex = |t: Option<TexRef>| t.and_then(|t| self.textures.get(t.texture).copied().flatten()).unwrap_or(self.white);
		let base_ref = m.base_texture;
		let (scale, offset) = match base_ref {
			Some(t) => (t.scale * uv_scale, t.offset * uv_scale + uv_offset),
			None => (uv_scale, uv_offset),
		};
		gl.uniform_2_f32(p.at("u_uv_scale"), scale.x, scale.y);
		gl.uniform_2_f32(p.at("u_uv_offset"), offset.x, offset.y);
		gl.active_texture(glow::TEXTURE0);
		match part.skin {
			Some(s) => {
				gl.bind_texture(glow::TEXTURE_2D, Some(self.joint_textures[s]));
				gl.uniform_1_i32(p.at("u_skinned"), 1);
				gl.uniform_matrix_4_f32_slice(p.at("u_model"), false, &Mat4::IDENTITY.to_cols_array());
			}
			None => {
				gl.bind_texture(glow::TEXTURE_2D, Some(self.identity_joints));
				gl.uniform_1_i32(p.at("u_skinned"), 0);
				gl.uniform_matrix_4_f32_slice(p.at("u_model"), false, &puppet.rig.world[part.node].to_cols_array());
			}
		}
		gl.active_texture(glow::TEXTURE1);
		gl.bind_texture(glow::TEXTURE_2D, Some(tex(base_ref)));
		let shade_from_base = m.shade_texture.is_some()
			&& (m.shade_texture.map(|t| t.texture) == base_ref.map(|t| t.texture)
				|| m.shade_texture.and_then(|t| self.textures.get(t.texture).copied().flatten()).is_none());
		gl.active_texture(glow::TEXTURE2);
		gl.bind_texture(glow::TEXTURE_2D, Some(if m.shade_texture.is_some() && !shade_from_base { tex(m.shade_texture) } else { self.white }));
		gl.active_texture(glow::TEXTURE3);
		gl.bind_texture(glow::TEXTURE_2D, Some(tex(m.emissive_texture)));
		gl.active_texture(glow::TEXTURE4);
		gl.bind_texture(glow::TEXTURE_2D, Some(self.white));
		gl.active_texture(glow::TEXTURE0);

		let base = colors[ColorKind::Base as usize];
		let shade = colors[ColorKind::Shade as usize];
		let emission = colors[ColorKind::Emission as usize];
		let rim = colors[ColorKind::Rim as usize];
		let line = colors[ColorKind::Outline as usize];
		gl.uniform_4_f32(p.at("u_base"), base[0], base[1], base[2], base[3]);
		gl.uniform_3_f32(p.at("u_shade"), shade[0], shade[1], shade[2]);
		gl.uniform_3_f32(p.at("u_emissive"), emission[0], emission[1], emission[2]);
		gl.uniform_3_f32(p.at("u_rim"), rim[0], rim[1], rim[2]);
		gl.uniform_1_f32(p.at("u_rim_power"), m.rim_power);
		gl.uniform_1_f32(p.at("u_rim_lift"), m.rim_lift);
		gl.uniform_1_f32(p.at("u_shift"), m.shading_shift);
		gl.uniform_1_f32(p.at("u_toony"), m.shading_toony.clamp(0.0, 0.999));
		gl.uniform_1_i32(p.at("u_unlit"), m.unlit as i32);
		gl.uniform_1_i32(p.at("u_shade_from_base"), shade_from_base as i32);
		let (mode, cutoff) = match m.alpha {
			AlphaMode::Opaque => (0, 0.0),
			AlphaMode::Mask(c) => (1, c),
			AlphaMode::Blend => (2, 0.0),
		};
		gl.uniform_1_i32(p.at("u_alpha_mode"), mode);
		gl.uniform_1_f32(p.at("u_cutoff"), cutoff);
		gl.uniform_1_i32(p.at("u_outline_pass"), outline as i32);
		gl.uniform_3_f32(p.at("u_outline_color"), line[0], line[1], line[2]);
		gl.uniform_1_f32(p.at("u_outline"), if outline { m.outline_width } else { 0.0 });
		gl.uniform_1_i32(p.at("u_outline_screen"), (m.outline_mode == OutlineMode::Screen) as i32);
		gl.uniform_1_i32(p.at("u_has_outline_tex"), 0);

		if mode == 2 {
			gl.enable(glow::BLEND);
			gl.blend_func_separate(glow::SRC_ALPHA, glow::ONE_MINUS_SRC_ALPHA, glow::ONE, glow::ONE_MINUS_SRC_ALPHA);
			gl.depth_mask(m.zwrite);
		} else {
			gl.disable(glow::BLEND);
			gl.depth_mask(true);
		}
		if outline {
			gl.enable(glow::CULL_FACE);
			gl.cull_face(glow::FRONT);
		} else if m.double_sided {
			gl.disable(glow::CULL_FACE);
		} else {
			gl.enable(glow::CULL_FACE);
			gl.cull_face(glow::BACK);
		}
		gl.bind_vertex_array(Some(part.vao));
		gl.draw_elements(glow::TRIANGLES, part.count, glow::UNSIGNED_INT, 0);
	}
}

impl Drop for VrmRenderer {
	fn drop(&mut self) {
		unsafe {
			for part in &self.parts {
				self.gl.delete_vertex_array(part.vao);
				self.gl.delete_buffer(part.indices);
			}
			for set in &self.points {
				for b in [set.positions, set.normals, set.uvs] {
					self.gl.delete_buffer(b);
				}
				if let Some((j, w)) = set.skinning {
					self.gl.delete_buffer(j);
					self.gl.delete_buffer(w);
				}
			}
			if let Some(o) = self.offscreen.take() {
				self.gl.delete_framebuffer(o.fbo);
				self.gl.delete_renderbuffer(o.color);
				self.gl.delete_renderbuffer(o.depth);
			}
			for t in self.textures.iter().flatten().chain(self.joint_textures.iter()) {
				self.gl.delete_texture(*t);
			}
			self.gl.delete_texture(self.white);
			self.gl.delete_texture(self.identity_joints);
			self.gl.delete_program(self.program.program);
		}
	}
}

/// Decode an image, cut down so neither side is over `side` pixels.
/// A picture's size, from its header (without decoding it).
fn image_size(glb: &super::gltf::Glb, image: usize) -> Option<(u32, u32)> {
	let (bytes, _) = glb.image(image).ok()?;
	image::ImageReader::new(std::io::Cursor::new(bytes)).with_guessed_format().ok()?.into_dimensions().ok()
}

fn decode(glb: &super::gltf::Glb, image: usize, side: u32) -> Result<(u32, u32, Vec<u8>), String> {
	let (bytes, _) = glb.image(image)?;
	let img = image::load_from_memory(bytes).map_err(|e| format!("cannot read the picture: {e}"))?;
	let img = if img.width() > side || img.height() > side {
		img.resize(side, side, image::imageops::FilterType::Triangle)
	} else {
		img
	};
	let rgba = img.to_rgba8();
	Ok((rgba.width(), rgba.height(), rgba.into_raw()))
}

/// After upload the processor keeps the points only of parts whose face shapes change.
fn release_static_data(puppet: &mut VrmPuppet) {
	for mesh in &mut puppet.model.meshes {
		// The renderer keeps its own copy of the points of sets whose shapes change; the face shapes stay.
		for p in &mut mesh.points {
			p.normals = Vec::new();
			p.uvs = Vec::new();
			p.positions = Vec::new();
			p.joints = Vec::new();
			p.weights = Vec::new();
		}
		for p in &mut mesh.primitives {
			p.indices = Vec::new();
		}
	}
}
