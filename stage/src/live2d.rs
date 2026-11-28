//! Plays Live2D-format characters (`.model3.json` with its `.moc3`, textures
//! and physics) with Ayagami's core, and draws them with OpenGL ES 3.
//!
//! Ayagami works out, every frame, where each art mesh's points are, how
//! opaque and what colour it is, and the order to draw in. This module draws
//! that the way Ayagami's own renderer does: premultiplied alpha, clipping
//! masks (also inverted ones), the classic additive and multiplicative blends,
//! the newer blend modes (worked out in the shader against a copy of what is
//! already drawn), parts drawn off screen first, and back-face culling.
//!
//! Positions given to the stage (`--view`, the framing box) are canvas pixels:
//! the model's own pixels, counted from the top-left corner of its canvas.

use std::panic::{catch_unwind, AssertUnwindSafe};
use std::path::{Component, Path, PathBuf};

use ayagami::core::{ArtMesh as _, Item as _, Model as _, Param as _, Part as _};
use ayagami::core::{AlphaBlendMode, BlendConfig, ColorBlendMode};
use ayagami::driver::{DrawNode, Driver, Visual};
use ayagami::file::ParsedModel;
use ayagami::meta::{Model3, Physics3};
use ayagami::physics::{PhysicsEngine, PhysicsOptions};
use ayagami::pose::{Key, Pose};
use glam::Vec2;
use glow::HasContext;

use crate::live2d_motion::{parse_motion, IdleMotions, PoseGroups};
use crate::puppet_info::{ParamInfo, PuppetInfo};

/// Files larger than this are refused before reading.
const MAX_FILE_BYTES: u64 = 256 << 20;
/// Textures wider or taller than this are refused; ones larger than the
/// graphics chip allows are scaled down to fit when they are uploaded.
const MAX_TEXTURE_SIDE: u32 = 16384;
/// At most this many idle motions are read (each is small; this only bounds a strange model).
const MAX_IDLE_MOTIONS: usize = 64;

/// Does this file look like a Live2D-format model description?
pub fn looks_like_model3(bytes: &[u8]) -> bool {
	let start = bytes.iter().position(|b| !b.is_ascii_whitespace()).unwrap_or(bytes.len());
	bytes.get(start) == Some(&b'{') && bytes.windows(14).any(|w| w == b"FileReferences")
}

/// A file named in the model description: relative, and inside the model's folder.
fn resolve(base: &Path, name: &str) -> Result<PathBuf, String> {
	let rel = Path::new(name);
	if name.is_empty() || rel.components().any(|c| !matches!(c, Component::Normal(_) | Component::CurDir)) {
		return Err(format!("the model names a file outside its folder: '{name}'"));
	}
	Ok(base.join(rel))
}

fn read_limited(path: &Path) -> Result<Vec<u8>, String> {
	let size = std::fs::metadata(path).map_err(|e| format!("cannot read {}: {e}", path.display()))?.len();
	if size > MAX_FILE_BYTES {
		return Err(format!("{} is too large ({size} bytes)", path.display()));
	}
	std::fs::read(path).map_err(|e| format!("cannot read {}: {e}", path.display()))
}

/// An RGBA picture with colours multiplied by alpha, ready to upload.
pub struct Picture {
	pub width: u32,
	pub height: u32,
	pub pixels: Vec<u8>,
}

fn decode_texture(path: &Path) -> Result<Picture, String> {
	let bytes = read_limited(path)?;
	let mut reader = image::ImageReader::new(std::io::Cursor::new(bytes));
	let mut limits = image::Limits::default();
	limits.max_image_width = Some(MAX_TEXTURE_SIDE);
	limits.max_image_height = Some(MAX_TEXTURE_SIDE);
	limits.max_alloc = Some(MAX_TEXTURE_SIDE as u64 * MAX_TEXTURE_SIDE as u64 * 4);
	reader.limits(limits);
	let reader = reader.with_guessed_format().map_err(|e| format!("cannot read {}: {e}", path.display()))?;
	let picture = reader.decode().map_err(|e| format!("cannot decode {}: {e}", path.display()))?.to_rgba8();
	let (width, height) = picture.dimensions();
	let mut pixels = picture.into_raw();
	premultiply(&mut pixels);
	Ok(Picture { width, height, pixels })
}

/// Live2D-format artwork is drawn with premultiplied alpha, as Ayagami and
/// Cubism do.
pub fn premultiply(pixels: &mut [u8]) {
	for px in pixels.chunks_exact_mut(4) {
		let a = px[3] as u32;
		for c in &mut px[..3] {
			*c = ((*c as u32 * a + 127) / 255) as u8;
		}
	}
}

/// How an expression's value combines with a parameter's own value.
#[derive(Clone, Copy, Debug, PartialEq)]
pub enum Blend {
	Add,
	Multiply,
	Overwrite,
}

/// One of the model's own expressions (`.exp3.json`): parameter settings the
/// model's artist made for a feeling, which a mood can show.
#[derive(Clone, Debug, PartialEq)]
pub struct Expression {
	pub name: String,
	pub params: Vec<(String, f32, Blend)>,
}

pub fn parse_expression(name: &str, bytes: &[u8]) -> Result<Expression, String> {
	let v: serde_json::Value = serde_json::from_slice(bytes).map_err(|e| format!("expression {name}: {e}"))?;
	let mut params = Vec::new();
	for p in v.get("Parameters").and_then(|p| p.as_array()).ok_or(format!("expression {name} has no parameters"))? {
		let id = p.get("Id").and_then(|i| i.as_str()).ok_or(format!("expression {name}: a parameter has no Id"))?;
		let value = p.get("Value").and_then(|x| x.as_f64()).ok_or(format!("expression {name}: {id} has no value"))? as f32;
		let blend = match p.get("Blend").and_then(|b| b.as_str()).unwrap_or("Add") {
			"Multiply" => Blend::Multiply,
			"Overwrite" => Blend::Overwrite,
			_ => Blend::Add,
		};
		params.push((id.to_string(), value, blend));
	}
	Ok(Expression { name: name.to_string(), params })
}

/// Blend an expression into this frame's parameter values at weight `w`
/// (0 to 1), as Cubism does: add, multiply or overwrite. Parameters not yet
/// in `values` start from `default`.
pub fn apply_expression(values: &mut Vec<(String, Vec2)>, default: impl Fn(&str) -> f32, e: &Expression, w: f32) {
	if w <= 0.0 {
		return;
	}
	for (id, value, blend) in &e.params {
		let i = match values.iter().position(|(n, _)| n == id) {
			Some(i) => i,
			None => {
				values.push((id.clone(), Vec2::new(default(id), 0.0)));
				values.len() - 1
			}
		};
		let v = &mut values[i].1.x;
		*v = match blend {
			Blend::Add => *v + value * w,
			Blend::Multiply => *v * (1.0 + (value - 1.0) * w),
			Blend::Overwrite => *v + (value - *v) * w,
		};
	}
}

/// What does not change while a model plays: one entry per art mesh, in the
/// model's own order (its uid).
#[derive(Clone, Debug)]
struct MeshInfo {
	texture: usize,
	blend: BlendConfig,
	culling: bool,
	invert_mask: bool,
	first_vertex: usize,
	vertex_count: usize,
	clips: Vec<u32>,
}

#[derive(Clone, Debug)]
struct PartInfo {
	blend: BlendConfig,
	invert_mask: bool,
	clips: Vec<u32>,
}

/// A loaded model and its moving state.
pub struct Live2dPuppet {
	model: ParsedModel,
	driver: Driver<ParsedModel>,
	physics: Option<PhysicsEngine>,
	settled: bool,
	textures: Vec<PathBuf>,
	pictures: Option<Vec<Picture>>,
	meshes: Vec<MeshInfo>,
	parts: Vec<PartInfo>,
	/// Mesh triangles as indices into the shared vertex list.
	indices: Vec<u32>,
	/// For each mesh, its range in `indices`.
	index_ranges: Vec<(usize, usize)>,
	texcoords: Vec<f32>,
	/// Canvas pixels per model unit, and the canvas position of the model's origin.
	ppu: f32,
	origin: [f32; 2],
	/// Anything at the top level drawn with a blend worked out in the shader:
	/// the model is then drawn off screen first, as Ayagami does.
	needs_offscreen: bool,
	pub info: PuppetInfo,
	/// Problems that do not stop the character playing, for the log.
	pub warnings: Vec<String>,
	/// The model's own expressions.
	pub expressions: Vec<Expression>,
	/// Its pose file's groups of parts shown one at a time (two sets of arms, say): the first of each
	/// group shows until one of the model's motions chooses another, as in Live2D's own player.
	pub pose: Option<PoseGroups>,
	/// The model's own idle motions, if it has any (`live2d_motion.rs`).
	pub idle: Option<IdleMotions>,
	/// Every group of motions the model has, and how many are in it (for the log and the settings page).
	pub motion_groups: Vec<(String, usize)>,
}

/// Which drawing features a model uses, for the log and for tests.
#[derive(Clone, Debug, Default, PartialEq)]
pub struct Features {
	pub meshes: usize,
	pub masked: usize,
	pub inverted_masks: usize,
	pub mask_sets: usize,
	pub culled: usize,
	pub additive: usize,
	pub multiplicative: usize,
	pub advanced_blends: usize,
	pub offscreen_parts: usize,
	pub physics: bool,
	/// Canvas width and height, origin and pixels per model unit.
	pub canvas: [f32; 5],
}

impl Features {
	pub fn to_json(&self) -> serde_json::Value {
		serde_json::json!({
			"meshes": self.meshes,
			"masked": self.masked,
			"inverted_masks": self.inverted_masks,
			"mask_sets": self.mask_sets,
			"culled": self.culled,
			"additive": self.additive,
			"multiplicative": self.multiplicative,
			"advanced_blends": self.advanced_blends,
			"offscreen_parts": self.offscreen_parts,
			"physics": self.physics,
			"canvas": {"width": self.canvas[0], "height": self.canvas[1], "origin": [self.canvas[2], self.canvas[3]], "ppu": self.canvas[4]},
		})
	}
}

impl Live2dPuppet {
	/// Load the model described by `path` (a `.model3.json`), with the files
	/// it names beside it.
	pub fn load(path: &Path) -> Result<Self, String> {
		// The device links to the model; read the files beside the real one.
		let real = std::fs::canonicalize(path).map_err(|e| format!("cannot read {}: {e}", path.display()))?;
		let base = real.parent().ok_or("the model has no folder")?.to_path_buf();
		let desc: Model3 = serde_json::from_slice(&read_limited(&real)?)
			.map_err(|e| format!("{} is not a Live2D model description: {e}", path.display()))?;
		let refs = &desc.file_references;
		let moc = read_limited(&resolve(&base, &refs.moc)?)?;
		let textures = refs.textures.iter().map(|t| resolve(&base, t)).collect::<Result<Vec<_>, _>>()?;
		if textures.is_empty() {
			return Err("the model lists no textures".into());
		}
		let mut warnings = Vec::new();
		let physics = match &refs.physics {
			None => None,
			Some(name) => match read_limited(&resolve(&base, name)?).and_then(|b| {
				serde_json::from_slice::<Physics3>(&b).map_err(|e| format!("the physics file cannot be read: {e}"))
			}) {
				Ok(setting) => Some(setting),
				Err(e) => {
					warnings.push(format!("{e}; playing without physics"));
					None
				}
			},
		};

		// Ayagami checks the file as it reads it; a panic deep inside would
		// still mean a file it cannot play, so report it the same way.
		let loaded = catch_unwind(AssertUnwindSafe(|| -> Result<_, String> {
			let model = ParsedModel::load(&mut moc.as_slice()).map_err(|e| format!("cannot load the model: {e}"))?;
			let mut driver = Driver::new(&model);
			driver.drive(&model);
			let physics = physics.map(|setting| PhysicsEngine::new(setting, PhysicsOptions::compatible(None)));
			Ok((model, driver, physics))
		}))
		.map_err(|_| "the model file is damaged or uses something the player cannot read".to_string())??;
		let (model, driver, physics) = loaded;

		// The model's own expressions, if it names any; one that cannot be read is skipped.
		let mut expressions = Vec::new();
		let index: serde_json::Value = serde_json::from_slice(&read_limited(&real)?).unwrap_or_default();
		if let Some(list) = index.get("FileReferences").and_then(|r| r.get("Expressions")).and_then(|e| e.as_array()) {
			for item in list {
				let (Some(name), Some(file)) = (item.get("Name").and_then(|n| n.as_str()), item.get("File").and_then(|f| f.as_str()))
				else {
					continue;
				};
				match resolve(&base, file).and_then(|p| read_limited(&p)).and_then(|b| parse_expression(name, &b)) {
					Ok(e) => expressions.push(e),
					Err(e) => warnings.push(e),
				}
			}
		}

		// Its pose file, if it names one: parts that take turns (a model drawn with two sets of arms).
		let mut pose = None;
		if let Some(file) = index.get("FileReferences").and_then(|r| r.get("Pose")).and_then(|p| p.as_str()) {
			match resolve(&base, file).and_then(|p| read_limited(&p)).and_then(|b| PoseGroups::parse(&b)) {
				Ok(groups) => pose = Some(groups),
				Err(e) => warnings.push(e),
			}
		}
		// Its own motions: the idle ones play under the device's movement.
		let (idle, motion_groups) = load_idle_motions(&index, &base, &mut warnings);

		let mut puppet = Self::describe(model, driver, physics, textures, path)?;
		puppet.pose = pose;
		puppet.idle = idle;
		puppet.motion_groups = motion_groups;
		puppet.warnings.extend(warnings);
		puppet.info.expressions = expressions.iter().map(|e| e.name.clone()).collect();
		puppet.expressions = expressions;
		puppet.pictures = Some(puppet.textures.iter().map(|p| decode_texture(p)).collect::<Result<_, _>>()?);
		Ok(puppet)
	}

	/// Gather what the drawing code needs from the model, and what the rest
	/// of the stage needs to know about it.
	fn describe(
		model: ParsedModel,
		driver: Driver<ParsedModel>,
		physics: Option<PhysicsEngine>,
		textures: Vec<PathBuf>,
		path: &Path,
	) -> Result<Self, String> {
		let texcoord_buffer = model.texcoord_buffer().unwrap_or(&[]);
		let index_buffer = model.index_buffer().unwrap_or(&[]);
		let mut meshes = Vec::new();
		let mut indices = Vec::new();
		let mut index_ranges = Vec::new();
		for (i, mesh) in model.artmeshes().into_iter().enumerate() {
			if mesh.uid() as usize != i {
				return Err("the model's art meshes are not numbered in order".into());
			}
			let first = mesh.texcoord_offset() as usize;
			let count = mesh.vertex_count() as usize;
			let range = mesh.index_range();
			let (start, end) = (range.start as usize, range.end as usize);
			if first + count > texcoord_buffer.len() || start > end || end > index_buffer.len() {
				return Err(format!("art mesh {} points outside the model's data", mesh.id()));
			}
			if mesh.texture() as usize >= textures.len() {
				return Err(format!("art mesh {} uses texture {} but the model lists {}", mesh.id(), mesh.texture(), textures.len()));
			}
			let at = indices.len();
			for &local in &index_buffer[start..end] {
				if local as usize >= count {
					return Err(format!("art mesh {} has a triangle outside its points", mesh.id()));
				}
				indices.push((first + local as usize) as u32);
			}
			index_ranges.push((at, end - start));
			meshes.push(MeshInfo {
				texture: mesh.texture() as usize,
				blend: mesh.blend_config(),
				culling: mesh.culling(),
				invert_mask: mesh.invert_mask(),
				first_vertex: first,
				vertex_count: count,
				clips: mesh.clips().into_iter().map(|c| c.uid()).collect(),
			});
		}
		let mut parts = Vec::new();
		for (i, part) in model.parts().into_iter().enumerate() {
			if part.uid() as usize != i {
				return Err("the model's parts are not numbered in order".into());
			}
			parts.push(PartInfo {
				blend: part.blend_config().unwrap_or_default(),
				invert_mask: part.invert_mask().unwrap_or(false),
				clips: part.clips().map(|c| c.into_iter().map(|m| m.uid()).collect()).unwrap_or_default(),
			});
		}
		let texcoords = texcoord_buffer.iter().flat_map(|c| [c.x, c.y]).collect();

		let canvas = model.canvas_properties();
		let ppu = if canvas.scale.is_finite() && canvas.scale > 0.0 { canvas.scale } else { 1.0 };
		let origin = [canvas.center.x, canvas.center.y];
		let params = model
			.params()
			.into_iter()
			.map(|p| ParamInfo {
				name: p.id().to_string(),
				is_vec2: false,
				min: [p.min(), 0.0],
				max: [p.max(), 0.0],
				defaults: [p.default(), 0.0],
			})
			.collect();

		// Frame the character by where its meshes are at rest, as the stage
		// does for Inochi2D puppets.
		let mut bounds: Option<[f32; 4]> = None;
		for mesh in model.artmeshes() {
			if let Some(state) = driver.artmesh_state(mesh.uid()) {
				for v in state.vertices {
					let (x, y) = (origin[0] + ppu * v.x, origin[1] + ppu * v.y);
					let b = bounds.get_or_insert([x, y, x, y]);
					b[0] = b[0].min(x);
					b[1] = b[1].min(y);
					b[2] = b[2].max(x);
					b[3] = b[3].max(y);
				}
			}
		}
		let needs_offscreen = driver.draw_nodes(None).unwrap_or(&[]).iter().any(|node| match node {
			DrawNode::ArtMesh(uid) => meshes[*uid as usize].blend.is_advanced(),
			DrawNode::OffscreenPart(uid) => parts[*uid as usize].blend.is_advanced(),
		});
		let name = path.file_name().and_then(|n| n.to_str()).map(|n| n.trim_end_matches(".model3.json").to_string());
		let info = PuppetInfo { name, params, bounds, part_count: meshes.len(), node_types: Vec::new(), expressions: Vec::new() };
		Ok(Self {
			model,
			driver,
			physics,
			settled: false,
			textures,
			pictures: None,
			meshes,
			parts,
			indices,
			index_ranges,
			texcoords,
			ppu,
			origin,
			needs_offscreen,
			info,
			warnings: Vec::new(),
			expressions: Vec::new(),
			pose: None,
			idle: None,
			motion_groups: Vec::new(),
		})
	}

	/// Set the parameters for this frame, step the physics and work out the
	/// meshes. Parameters not given take their defaults.
	/// Play without physics (for comparing drawings).
	pub fn drop_physics(&mut self) {
		self.physics = None;
	}

	pub fn features(&self) -> Features {
		let mut sets: Vec<Vec<u32>> = Vec::new();
		for clips in self.meshes.iter().map(|m| &m.clips).chain(self.parts.iter().map(|p| &p.clips)) {
			clip_set(&mut sets, clips);
		}
		let offscreen = self.model.parts().into_iter().filter(|p| p.offscreen()).count();
		Features {
			meshes: self.meshes.len(),
			masked: self.meshes.iter().filter(|m| !m.clips.is_empty()).count(),
			inverted_masks: self.meshes.iter().filter(|m| !m.clips.is_empty() && m.invert_mask).count(),
			mask_sets: sets.len(),
			culled: self.meshes.iter().filter(|m| m.culling).count(),
			additive: self.meshes.iter().filter(|m| m.blend.color == ColorBlendMode::PremultAdd).count(),
			multiplicative: self.meshes.iter().filter(|m| m.blend.color == ColorBlendMode::PremultMultiply).count(),
			advanced_blends: self.meshes.iter().filter(|m| m.blend.is_advanced()).count()
				+ self.parts.iter().filter(|p| p.blend.is_advanced()).count(),
			offscreen_parts: offscreen,
			physics: self.physics.is_some(),
			canvas: {
				let c = self.model.canvas_properties();
				[c.dimensions.x, c.dimensions.y, self.origin[0], self.origin[1], self.ppu]
			},
		}
	}

	pub fn update(&mut self, values: &[(String, Vec2)], dt: f32) {
		let mut pose = Pose::new(&self.model);
		for (name, value) in values {
			pose.set(&Key::param(name), value.x);
		}
		if let Some(groups) = &mut self.pose {
			groups.update(dt);
			for (part, opacity) in groups.opacities() {
				pose.set(&Key::part(&part), opacity);
			}
		}
		if let Some(physics) = &mut self.physics {
			if !self.settled {
				physics.settle(&pose);
				self.settled = true;
			}
			physics.update(&mut pose, dt);
		}
		self.driver.set_pose(&pose);
		self.driver.drive(&self.model);
	}

	/// Move the model's own idle motions on by `dt` seconds, and say how far each parameter they move
	/// is from its default now. Their choices of parts (which set of arms) go to the pose.
	pub fn own_motion(&mut self, dt: f32) -> Vec<(String, f32)> {
		let Some(idle) = &mut self.idle else { return Vec::new() };
		idle.step(dt);
		let params = &self.info.params;
		idle.changes(|id| params.iter().find(|p| p.name == id).map(|p| p.defaults[0]), self.pose.as_mut())
	}

	fn pictures(&mut self) -> Result<Vec<Picture>, String> {
		match self.pictures.take() {
			Some(p) => Ok(p),
			None => self.textures.iter().map(|p| decode_texture(p)).collect(),
		}
	}
}

// ------------------------------------------------------------------------------------------------
// Drawing
// ------------------------------------------------------------------------------------------------

const VERTEX: &str = r#"
layout(location = 0) in vec2 a_pos;
layout(location = 1) in vec2 a_uv;
uniform vec4 u_xform;
out vec2 v_uv;
void main() {
	v_uv = a_uv;
	gl_Position = vec4(a_pos * u_xform.xy + u_xform.zw, 0.0, 1.0);
}
"#;

/// One triangle over the whole target, for drawing a part's off-screen picture.
const VERTEX_FULL: &str = r#"
out vec2 v_uv;
void main() {
	v_uv = vec2(0.0);
	gl_Position = vec4(float((gl_VertexID & 1) << 2) - 1.0, float((gl_VertexID & 2) << 1) - 1.0, 0.0, 1.0);
}
"#;

const FRAGMENT_MASK: &str = r#"
uniform sampler2D u_tex;
in vec2 v_uv;
layout(location = 0) out vec4 o_color;
void main() {
	o_color = vec4(texture(u_tex, v_uv).a);
}
"#;

/// Colours, opacity, masks and blend modes; the advanced blends follow the
/// W3C compositing formulas that Cubism's blend modes are named after.
const FRAGMENT: &str = r#"
uniform sampler2D u_tex;
uniform sampler2D u_mask;
uniform sampler2D u_backdrop;
uniform int u_whole;
uniform int u_mask_mode;
uniform int u_advanced;
uniform int u_color_blend;
uniform int u_alpha_blend;
uniform vec3 u_multiply;
uniform vec3 u_screen;
uniform float u_opacity;
in vec2 v_uv;
layout(location = 0) out vec4 o_color;

vec4 sample_color() {
	vec4 p = u_whole != 0 ? texelFetch(u_tex, ivec2(gl_FragCoord.xy), 0) : texture(u_tex, v_uv);
	vec3 rgb = p.rgb * u_multiply;
	rgb = vec3(p.a) - (vec3(p.a) - rgb) * (vec3(1.0) - u_screen);
	return clamp(vec4(rgb, p.a), 0.0, 1.0);
}

float mask_value() {
	if (u_mask_mode == 0) return 1.0;
	float m = texelFetch(u_mask, ivec2(gl_FragCoord.xy), 0).r;
	return u_mask_mode == 2 ? 1.0 - m : m;
}

vec3 b_multiply(vec3 b, vec3 s) { return s * b; }
vec3 b_screen(vec3 b, vec3 s) { return b + s - b * s; }
vec3 b_hard_light(vec3 b, vec3 s) {
	return mix(b_screen(b, 2.0 * s - 1.0), b_multiply(b, 2.0 * s), vec3(lessThanEqual(s, vec3(0.5))));
}
vec3 soft_light_d(vec3 b) {
	return mix(sqrt(b), ((16.0 * b - 12.0) * b + 4.0) * b, vec3(lessThanEqual(b, vec3(0.25))));
}
float lum(vec3 c) { return 0.3 * c.r + 0.59 * c.g + 0.11 * c.b; }
vec3 clip_color(vec3 c) {
	float l = lum(c);
	float n = min(min(c.r, c.g), c.b);
	float x = max(max(c.r, c.g), c.b);
	if (n < 0.0) c = l + (c - l) * l / (l - n);
	if (x > 1.0) c = l + (c - l) * (1.0 - l) / (x - l);
	return c;
}
vec3 set_lum(vec3 c, float l) { return clip_color(c + (l - lum(c))); }
float sat(vec3 c) { return max(max(c.r, c.g), c.b) - min(min(c.r, c.g), c.b); }
vec3 set_sat(vec3 c, float s) {
	float mx = max(max(c.r, c.g), c.b);
	float mn = min(min(c.r, c.g), c.b);
	return mx > mn ? (c - mn) * s / (mx - mn) : vec3(0.0);
}

vec3 blend_color(vec3 cb, vec3 cs) {
	if (u_color_blend == 3) return clamp(cb + cs, 0.0, 1.0);                  // add
	if (u_color_blend == 4) return cb + cs;                                     // add (glow)
	if (u_color_blend == 5) return min(cb, cs);                                 // darken
	if (u_color_blend == 6) return b_multiply(cb, cs);                          // multiply
	if (u_color_blend == 7) {                                                   // colour burn
		vec3 burn = 1.0 - min(vec3(1.0), (1.0 - cb) / max(cs, vec3(1e-6)));
		return mix(mix(burn, vec3(0.0), vec3(equal(cs, vec3(0.0)))), vec3(1.0), vec3(equal(cb, vec3(1.0))));
	}
	if (u_color_blend == 8) return clamp(cb + cs - 1.0, 0.0, 1.0);             // linear burn
	if (u_color_blend == 9) return max(cb, cs);                                 // lighten
	if (u_color_blend == 10) return b_screen(cb, cs);                           // screen
	if (u_color_blend == 11) {                                                  // colour dodge
		vec3 dodge = min(vec3(1.0), cb / max(1.0 - cs, vec3(1e-6)));
		return mix(mix(dodge, vec3(1.0), vec3(equal(cs, vec3(1.0)))), vec3(0.0), vec3(equal(cb, vec3(0.0))));
	}
	if (u_color_blend == 12) return b_hard_light(cs, cb);                       // overlay
	if (u_color_blend == 13) {                                                  // soft light
		return mix(cb + (2.0 * cs - 1.0) * (soft_light_d(cb) - cb),
		           cb - (1.0 - 2.0 * cs) * cb * (1.0 - cb),
		           vec3(lessThanEqual(cs, vec3(0.5))));
	}
	if (u_color_blend == 14) return b_hard_light(cb, cs);                       // hard light
	if (u_color_blend == 15) return clamp(cb + 2.0 * cs - 1.0, 0.0, 1.0);      // linear light
	if (u_color_blend == 16) return set_lum(set_sat(cs, sat(cb)), lum(cb));     // hue
	if (u_color_blend == 17) return set_lum(cs, lum(cb));                       // colour
	return cs;                                                                  // normal
}

vec4 advanced(vec4 color, float k) {
	vec4 src = color * k;
	vec4 dst = texelFetch(u_backdrop, ivec2(gl_FragCoord.xy), 0);
	float as_ = src.a;
	float ab = dst.a;
	vec3 cs = as_ > 0.0 ? src.rgb / as_ : vec3(0.0);
	vec3 cb = ab > 0.0 ? dst.rgb / ab : vec3(0.0);
	vec3 cr = (1.0 - ab) * cs + ab * blend_color(cb, cs);
	if (u_alpha_blend == 1) return vec4(as_ * cr * ab + ab * cb * (1.0 - as_), ab);                 // atop
	if (u_alpha_blend == 2) { float ao = ab * (1.0 - as_); return vec4(ao * cb, ao); }              // out
	if (u_alpha_blend == 3) return vec4(as_ * cr + clamp(ab - as_, 0.0, 1.0) * cb, max(as_, ab));   // conjoint
	if (u_alpha_blend == 4) return vec4(as_ * cr + min(ab, 1.0 - as_) * cb, clamp(as_ + ab, 0.0, 1.0)); // disjoint
	return vec4(as_ * cr + ab * cb * (1.0 - as_), as_ + ab * (1.0 - as_));                         // over
}

void main() {
	vec4 p = sample_color();
	float k = u_opacity * mask_value();
	o_color = u_advanced != 0 ? advanced(p, k) : p * k;
}
"#;

pub(crate) fn header(gl: &glow::Context) -> &'static str {
	if gl.version().is_embedded {
		"#version 300 es\nprecision highp float;\nprecision highp int;\n"
	} else {
		"#version 330 core\n"
	}
}

pub(crate) unsafe fn compile(gl: &glow::Context, vertex: &str, fragment: &str) -> Result<glow::Program, String> {
	let program = gl.create_program()?;
	let mut shaders = Vec::new();
	for (kind, source) in [(glow::VERTEX_SHADER, vertex), (glow::FRAGMENT_SHADER, fragment)] {
		let shader = gl.create_shader(kind)?;
		gl.shader_source(shader, &format!("{}{}", header(gl), source));
		gl.compile_shader(shader);
		if !gl.get_shader_compile_status(shader) {
			return Err(format!("shader did not compile: {}", gl.get_shader_info_log(shader)));
		}
		gl.attach_shader(program, shader);
		shaders.push(shader);
	}
	gl.link_program(program);
	if !gl.get_program_link_status(program) {
		return Err(format!("shaders did not link: {}", gl.get_program_info_log(program)));
	}
	for shader in shaders {
		gl.detach_shader(program, shader);
		gl.delete_shader(shader);
	}
	Ok(program)
}

/// A drawing program and where its settings go.
struct Program {
	program: glow::Program,
	xform: Option<glow::UniformLocation>,
	tex: Option<glow::UniformLocation>,
	mask: Option<glow::UniformLocation>,
	backdrop: Option<glow::UniformLocation>,
	whole: Option<glow::UniformLocation>,
	mask_mode: Option<glow::UniformLocation>,
	advanced: Option<glow::UniformLocation>,
	color_blend: Option<glow::UniformLocation>,
	alpha_blend: Option<glow::UniformLocation>,
	multiply: Option<glow::UniformLocation>,
	screen: Option<glow::UniformLocation>,
	opacity: Option<glow::UniformLocation>,
}

impl Program {
	unsafe fn new(gl: &glow::Context, vertex: &str, fragment: &str) -> Result<Self, String> {
		let program = compile(gl, vertex, fragment)?;
		let at = |name: &str| gl.get_uniform_location(program, name);
		let p = Self {
			program,
			xform: at("u_xform"),
			tex: at("u_tex"),
			mask: at("u_mask"),
			backdrop: at("u_backdrop"),
			whole: at("u_whole"),
			mask_mode: at("u_mask_mode"),
			advanced: at("u_advanced"),
			color_blend: at("u_color_blend"),
			alpha_blend: at("u_alpha_blend"),
			multiply: at("u_multiply"),
			screen: at("u_screen"),
			opacity: at("u_opacity"),
		};
		gl.use_program(Some(program));
		gl.uniform_1_i32(p.tex.as_ref(), 0);
		gl.uniform_1_i32(p.mask.as_ref(), 1);
		gl.uniform_1_i32(p.backdrop.as_ref(), 2);
		Ok(p)
	}
}

/// A picture the size of the screen that can be drawn into.
struct Target {
	fbo: glow::Framebuffer,
	tex: glow::Texture,
}

unsafe fn make_target(gl: &glow::Context, width: u32, height: u32, single_channel: bool) -> Result<Target, String> {
	let tex = gl.create_texture()?;
	gl.bind_texture(glow::TEXTURE_2D, Some(tex));
	let (internal, format) = if single_channel { (glow::R8, glow::RED) } else { (glow::RGBA8, glow::RGBA) };
	gl.tex_image_2d(glow::TEXTURE_2D, 0, internal as i32, width as i32, height as i32, 0, format, glow::UNSIGNED_BYTE, None);
	for (k, v) in [
		(glow::TEXTURE_MIN_FILTER, glow::NEAREST),
		(glow::TEXTURE_MAG_FILTER, glow::NEAREST),
		(glow::TEXTURE_WRAP_S, glow::CLAMP_TO_EDGE),
		(glow::TEXTURE_WRAP_T, glow::CLAMP_TO_EDGE),
	] {
		gl.tex_parameter_i32(glow::TEXTURE_2D, k, v as i32);
	}
	let fbo = gl.create_framebuffer()?;
	gl.bind_framebuffer(glow::FRAMEBUFFER, Some(fbo));
	gl.framebuffer_texture_2d(glow::FRAMEBUFFER, glow::COLOR_ATTACHMENT0, glow::TEXTURE_2D, Some(tex), 0);
	let status = gl.check_framebuffer_status(glow::FRAMEBUFFER);
	if status != glow::FRAMEBUFFER_COMPLETE {
		return Err(format!("cannot draw off screen (framebuffer status {status:#x})"));
	}
	Ok(Target { fbo, tex })
}

/// The view: which canvas point is at the middle of the screen, and how many
/// screen pixels one canvas pixel takes.
#[derive(Clone, Copy, Debug)]
pub struct Camera {
	pub centre: Vec2,
	pub scale: f32,
}

pub struct Live2dRenderer {
	gl: glow::Context,
	mesh: Program,
	mask: Program,
	whole: Program,
	vao: glow::VertexArray,
	positions: glow::Buffer,
	textures: Vec<glow::Texture>,
	/// Distinct lists of masking meshes, and which list each mesh and part uses.
	clip_sets: Vec<Vec<u32>>,
	mesh_clip: Vec<Option<usize>>,
	part_clip: Vec<Option<usize>>,
	masks: Vec<Option<Target>>,
	pool: Vec<Target>,
	backdrop: Option<Target>,
	vertices: Vec<f32>,
	width: u32,
	height: u32,
	pub camera: Camera,
	pub background: [f32; 3],
}

fn clip_set(sets: &mut Vec<Vec<u32>>, clips: &[u32]) -> Option<usize> {
	if clips.is_empty() {
		return None;
	}
	Some(sets.iter().position(|s| s == clips).unwrap_or_else(|| {
		sets.push(clips.to_vec());
		sets.len() - 1
	}))
}

impl Live2dRenderer {
	pub fn new(gl: glow::Context, puppet: &mut Live2dPuppet) -> Result<Self, String> {
		let pictures = puppet.pictures()?;
		unsafe {
			let mesh = Program::new(&gl, VERTEX, FRAGMENT)?;
			let mask = Program::new(&gl, VERTEX, FRAGMENT_MASK)?;
			let whole = Program::new(&gl, VERTEX_FULL, FRAGMENT)?;

			let vao = gl.create_vertex_array()?;
			gl.bind_vertex_array(Some(vao));
			let total = puppet.texcoords.len() / 2;
			let positions = gl.create_buffer()?;
			gl.bind_buffer(glow::ARRAY_BUFFER, Some(positions));
			gl.buffer_data_size(glow::ARRAY_BUFFER, (total * 8).max(8) as i32, glow::DYNAMIC_DRAW);
			gl.enable_vertex_attrib_array(0);
			gl.vertex_attrib_pointer_f32(0, 2, glow::FLOAT, false, 8, 0);
			let texcoords = gl.create_buffer()?;
			gl.bind_buffer(glow::ARRAY_BUFFER, Some(texcoords));
			gl.buffer_data_u8_slice(glow::ARRAY_BUFFER, bytes_of(&puppet.texcoords), glow::STATIC_DRAW);
			gl.enable_vertex_attrib_array(1);
			gl.vertex_attrib_pointer_f32(1, 2, glow::FLOAT, false, 8, 0);
			let indices = gl.create_buffer()?;
			gl.bind_buffer(glow::ELEMENT_ARRAY_BUFFER, Some(indices));
			gl.buffer_data_u8_slice(glow::ELEMENT_ARRAY_BUFFER, bytes_of(&puppet.indices), glow::STATIC_DRAW);

			let max_side = gl.get_parameter_i32(glow::MAX_TEXTURE_SIZE).max(1024) as u32;
			let mut textures = Vec::new();
			for picture in pictures {
				let picture = fit_texture(picture, max_side);
				let tex = gl.create_texture()?;
				gl.bind_texture(glow::TEXTURE_2D, Some(tex));
				gl.pixel_store_i32(glow::UNPACK_ALIGNMENT, 1);
				gl.tex_image_2d(
					glow::TEXTURE_2D,
					0,
					glow::RGBA8 as i32,
					picture.width as i32,
					picture.height as i32,
					0,
					glow::RGBA,
					glow::UNSIGNED_BYTE,
					Some(&picture.pixels),
				);
				gl.generate_mipmap(glow::TEXTURE_2D);
				for (k, v) in [
					(glow::TEXTURE_MIN_FILTER, glow::LINEAR_MIPMAP_LINEAR),
					(glow::TEXTURE_MAG_FILTER, glow::LINEAR),
					(glow::TEXTURE_WRAP_S, glow::CLAMP_TO_EDGE),
					(glow::TEXTURE_WRAP_T, glow::CLAMP_TO_EDGE),
				] {
					gl.tex_parameter_i32(glow::TEXTURE_2D, k, v as i32);
				}
				textures.push(tex);
			}

			let mut clip_sets = Vec::new();
			let mesh_clip = puppet.meshes.iter().map(|m| clip_set(&mut clip_sets, &m.clips)).collect();
			let part_clip = puppet.parts.iter().map(|p| clip_set(&mut clip_sets, &p.clips)).collect();
			let masks = clip_sets.iter().map(|_| None).collect();
			Ok(Self {
				gl,
				mesh,
				mask,
				whole,
				vao,
				positions,
				textures,
				clip_sets,
				mesh_clip,
				part_clip,
				masks,
				pool: Vec::new(),
				backdrop: None,
				vertices: vec![0.0; total * 2],
				width: 1,
				height: 1,
				camera: Camera { centre: Vec2::ZERO, scale: 1.0 },
				background: [0.0, 0.0, 0.0],
			})
		}
	}

	pub fn resize(&mut self, width: u32, height: u32) {
		self.width = width.max(1);
		self.height = height.max(1);
		// Off-screen pictures match the screen; make them again at the new size.
		unsafe {
			for target in self.masks.iter_mut().filter_map(Option::take).chain(self.pool.drain(..)).chain(self.backdrop.take()) {
				self.gl.delete_framebuffer(target.fbo);
				self.gl.delete_texture(target.tex);
			}
		}
	}

	/// Model units to clip space, through canvas pixels and the camera.
	fn xform(&self, ppu: f32, origin: [f32; 2]) -> [f32; 4] {
		let (w, h) = (self.width as f32, self.height as f32);
		let s = self.camera.scale;
		let sx = 2.0 * s * ppu / w;
		let sy = -2.0 * s * ppu / h;
		let tx = 2.0 * s * (origin[0] - self.camera.centre.x) / w;
		let ty = -2.0 * s * (origin[1] - self.camera.centre.y) / h;
		[sx, sy, tx, ty]
	}

	fn take_target(&mut self) -> Result<Target, String> {
		match self.pool.pop() {
			Some(t) => Ok(t),
			None => unsafe { make_target(&self.gl, self.width, self.height, false) },
		}
	}

	/// Draw the model's current frame over the background.
	pub fn draw(&mut self, puppet: &Live2dPuppet, background: Option<&crate::background::Background>) -> Result<(), String> {
		let gl = &self.gl;
		unsafe {
			gl.bind_vertex_array(Some(self.vao));
			gl.disable(glow::DEPTH_TEST);
			gl.disable(glow::SCISSOR_TEST);
			gl.disable(glow::STENCIL_TEST);
			gl.color_mask(true, true, true, true);
			// The same faces as Ayagami culls (counter-clockwise front, front
			// culled), checked on Live2D's sample models that use culling.
			gl.front_face(glow::CCW);
			gl.cull_face(glow::FRONT);
		}

		// Where every mesh is this frame.
		let mut changed = false;
		for (uid, mesh) in puppet.meshes.iter().enumerate() {
			if let Some(state) = puppet.driver.artmesh_state(uid as u32) {
				if state.vertices.len() == mesh.vertex_count {
					let out = &mut self.vertices[mesh.first_vertex * 2..(mesh.first_vertex + mesh.vertex_count) * 2];
					for (pair, v) in out.chunks_exact_mut(2).zip(state.vertices) {
						pair[0] = v.x;
						pair[1] = v.y;
					}
					changed = true;
				}
			}
		}
		if changed {
			unsafe {
				gl.bind_buffer(glow::ARRAY_BUFFER, Some(self.positions));
				gl.buffer_sub_data_u8_slice(glow::ARRAY_BUFFER, 0, bytes_of(&self.vertices));
			}
		}

		let xform = self.xform(puppet.ppu, puppet.origin);
		self.draw_masks(puppet, xform)?;

		unsafe {
			let gl = &self.gl;
			gl.bind_framebuffer(glow::FRAMEBUFFER, None);
			gl.viewport(0, 0, self.width as i32, self.height as i32);
			let [r, g, b] = self.background;
			gl.clear_color(r, g, b, 1.0);
			gl.clear(glow::COLOR_BUFFER_BIT);
			if let Some(picture) = background {
				picture.draw(gl, self.width, self.height);
			}
		}
		if puppet.needs_offscreen {
			let top = self.take_target()?;
			self.draw_into(puppet, None, &top, xform)?;
			unsafe { self.gl.bind_framebuffer(glow::FRAMEBUFFER, None) };
			self.draw_whole(&top, None, &BlendConfig::default(), Look::PLAIN, None)?;
			self.pool.push(top);
		} else {
			self.draw_nodes(puppet, None, None, xform)?;
		}
		unsafe {
			self.gl.disable(glow::CULL_FACE);
			self.gl.bind_vertex_array(None);
		}
		Ok(())
	}

	/// Draw every mask that a visible mesh or part uses this frame.
	fn draw_masks(&mut self, puppet: &Live2dPuppet, xform: [f32; 4]) -> Result<(), String> {
		let mut used = vec![false; self.clip_sets.len()];
		let mut queue = vec![None];
		while let Some(part) = queue.pop() {
			for node in puppet.driver.draw_nodes(part).unwrap_or(&[]) {
				match node {
					DrawNode::ArtMesh(uid) => {
						let Some(state) = puppet.driver.artmesh_state(*uid) else { continue };
						if state.visual.visible && state.visual.opacity > 0.0 {
							if let Some(k) = self.mesh_clip[*uid as usize] {
								used[k] = true;
							}
						}
					}
					DrawNode::OffscreenPart(uid) => {
						let visual = puppet.driver.part_state(*uid).and_then(|s| s.visual);
						if visual.is_some_and(|v| v.visible && v.opacity > 0.0) {
							if let Some(k) = self.part_clip[*uid as usize] {
								used[k] = true;
							}
							queue.push(Some(*uid));
						}
					}
				}
			}
		}
		for (k, in_use) in used.into_iter().enumerate() {
			if !in_use {
				continue;
			}
			if self.masks[k].is_none() {
				self.masks[k] = Some(unsafe { make_target(&self.gl, self.width, self.height, true)? });
			}
			let gl = &self.gl;
			let target = self.masks[k].as_ref().unwrap();
			unsafe {
				gl.bind_framebuffer(glow::FRAMEBUFFER, Some(target.fbo));
				gl.viewport(0, 0, self.width as i32, self.height as i32);
				gl.clear_color(0.0, 0.0, 0.0, 0.0);
				gl.clear(glow::COLOR_BUFFER_BIT);
				gl.use_program(Some(self.mask.program));
				gl.uniform_4_f32(self.mask.xform.as_ref(), xform[0], xform[1], xform[2], xform[3]);
				gl.enable(glow::BLEND);
				gl.blend_func_separate(glow::ONE, glow::ONE_MINUS_SRC_COLOR, glow::ONE, glow::ONE_MINUS_SRC_ALPHA);
				gl.active_texture(glow::TEXTURE0);
				for &uid in &self.clip_sets[k] {
					let Some(state) = puppet.driver.artmesh_state(uid) else { continue };
					// Masks ignore opacity but not visibility, as in Cubism.
					if !state.visual.visible {
						continue;
					}
					let mesh = &puppet.meshes[uid as usize];
					set_culling(gl, mesh.culling);
					gl.bind_texture(glow::TEXTURE_2D, Some(self.textures[mesh.texture]));
					let (start, count) = puppet.index_ranges[uid as usize];
					gl.draw_elements(glow::TRIANGLES, count as i32, glow::UNSIGNED_INT, (start * 4) as i32);
				}
			}
		}
		Ok(())
	}

	/// Draw the nodes of `part` (the whole model for `None`) into `target`
	/// (the screen for `None`), which is already cleared or holds what is
	/// behind them.
	fn draw_nodes(&mut self, puppet: &Live2dPuppet, part: Option<u32>, target: Option<&Target>, xform: [f32; 4]) -> Result<(), String> {
		let nodes = puppet.driver.draw_nodes(part).unwrap_or(&[]).to_vec();
		for node in nodes {
			match node {
				DrawNode::ArtMesh(uid) => {
					let Some(state) = puppet.driver.artmesh_state(uid) else { continue };
					if !state.visual.visible || state.visual.opacity <= 0.0 {
						continue;
					}
					let mesh = &puppet.meshes[uid as usize];
					let advanced = mesh.blend.is_advanced();
					if advanced {
						self.copy_backdrop(target)?;
					}
					let gl = &self.gl;
					let p = &self.mesh;
					unsafe {
						gl.use_program(Some(p.program));
						gl.uniform_4_f32(p.xform.as_ref(), xform[0], xform[1], xform[2], xform[3]);
						gl.uniform_1_i32(p.whole.as_ref(), 0);
						set_look(gl, p, Look::of(&state.visual), &mesh.blend, mask_mode(self.mesh_clip[uid as usize], mesh.invert_mask));
						self.bind_extras(self.mesh_clip[uid as usize], advanced);
						gl.active_texture(glow::TEXTURE0);
						gl.bind_texture(glow::TEXTURE_2D, Some(self.textures[mesh.texture]));
						set_culling(gl, mesh.culling);
						let (start, count) = puppet.index_ranges[uid as usize];
						gl.draw_elements(glow::TRIANGLES, count as i32, glow::UNSIGNED_INT, (start * 4) as i32);
					}
				}
				DrawNode::OffscreenPart(uid) => {
					let Some(visual) = puppet.driver.part_state(uid).and_then(|s| s.visual) else { continue };
					if !visual.visible || visual.opacity <= 0.0 {
						continue;
					}
					let picture = self.take_target()?;
					self.draw_into(puppet, Some(uid), &picture, xform)?;
					unsafe {
						self.gl.bind_framebuffer(glow::FRAMEBUFFER, target.map(|t| t.fbo));
					}
					let info = &puppet.parts[uid as usize];
					if info.blend.is_advanced() {
						self.copy_backdrop(target)?;
					}
					let clip = self.part_clip[uid as usize];
					self.draw_whole(&picture, clip, &info.blend, Look::of(&visual), Some(info.invert_mask))?;
					self.pool.push(picture);
				}
			}
		}
		Ok(())
	}

	/// Draw a part (or the whole model) on its own, into a cleared picture.
	fn draw_into(&mut self, puppet: &Live2dPuppet, part: Option<u32>, picture: &Target, xform: [f32; 4]) -> Result<(), String> {
		unsafe {
			let gl = &self.gl;
			gl.bind_framebuffer(glow::FRAMEBUFFER, Some(picture.fbo));
			gl.viewport(0, 0, self.width as i32, self.height as i32);
			gl.clear_color(0.0, 0.0, 0.0, 0.0);
			gl.clear(glow::COLOR_BUFFER_BIT);
		}
		self.draw_nodes(puppet, part, Some(picture), xform)
	}

	/// Copy what is drawn so far, for a blend worked out in the shader.
	fn copy_backdrop(&mut self, target: Option<&Target>) -> Result<(), String> {
		if self.backdrop.is_none() {
			self.backdrop = Some(unsafe { make_target(&self.gl, self.width, self.height, false)? });
		}
		let gl = &self.gl;
		let backdrop = self.backdrop.as_ref().unwrap();
		unsafe {
			gl.bind_framebuffer(glow::FRAMEBUFFER, target.map(|t| t.fbo));
			gl.active_texture(glow::TEXTURE2);
			gl.bind_texture(glow::TEXTURE_2D, Some(backdrop.tex));
			gl.copy_tex_sub_image_2d(glow::TEXTURE_2D, 0, 0, 0, 0, 0, self.width as i32, self.height as i32);
			gl.active_texture(glow::TEXTURE0);
		}
		Ok(())
	}

	/// Bind the mask and the copied backdrop a draw needs.
	unsafe fn bind_extras(&self, clip: Option<usize>, advanced: bool) {
		let gl = &self.gl;
		if let Some(k) = clip {
			if let Some(mask) = &self.masks[k] {
				gl.active_texture(glow::TEXTURE1);
				gl.bind_texture(glow::TEXTURE_2D, Some(mask.tex));
			}
		}
		if advanced {
			if let Some(backdrop) = &self.backdrop {
				gl.active_texture(glow::TEXTURE2);
				gl.bind_texture(glow::TEXTURE_2D, Some(backdrop.tex));
			}
		}
		gl.active_texture(glow::TEXTURE0);
	}

	/// Put a screen-sized picture onto the current target.
	fn draw_whole(&self, picture: &Target, clip: Option<usize>, blend: &BlendConfig, look: Look, invert: Option<bool>) -> Result<(), String> {
		let gl = &self.gl;
		let p = &self.whole;
		unsafe {
			gl.viewport(0, 0, self.width as i32, self.height as i32);
			gl.use_program(Some(p.program));
			gl.uniform_1_i32(p.whole.as_ref(), 1);
			set_look(gl, p, look, blend, mask_mode(clip, invert.unwrap_or(false)));
			self.bind_extras(clip, blend.is_advanced());
			gl.active_texture(glow::TEXTURE0);
			gl.bind_texture(glow::TEXTURE_2D, Some(picture.tex));
			gl.disable(glow::CULL_FACE);
			gl.draw_arrays(glow::TRIANGLES, 0, 3);
		}
		Ok(())
	}
}

impl Drop for Live2dRenderer {
	fn drop(&mut self) {
		self.resize(1, 1);
		unsafe {
			for tex in self.textures.drain(..) {
				self.gl.delete_texture(tex);
			}
		}
	}
}

/// How one draw looks: opacity and Cubism's multiply and screen colours.
#[derive(Clone, Copy, Debug)]
struct Look {
	opacity: f32,
	multiply: [f32; 3],
	screen: [f32; 3],
}

impl Look {
	const PLAIN: Look = Look { opacity: 1.0, multiply: [1.0; 3], screen: [0.0; 3] };

	fn of(visual: &Visual) -> Self {
		Look { opacity: visual.opacity, multiply: visual.multiply_color.to_array(), screen: visual.screen_color.to_array() }
	}
}

fn mask_mode(clip: Option<usize>, invert: bool) -> i32 {
	match (clip, invert) {
		(None, _) => 0,
		(Some(_), false) => 1,
		(Some(_), true) => 2,
	}
}

unsafe fn set_culling(gl: &glow::Context, culling: bool) {
	if culling {
		gl.enable(glow::CULL_FACE);
	} else {
		gl.disable(glow::CULL_FACE);
	}
}

/// Colour, opacity and blending for one draw.
unsafe fn set_look(gl: &glow::Context, p: &Program, look: Look, blend: &BlendConfig, mask_mode: i32) {
	let (m, s) = (look.multiply, look.screen);
	gl.uniform_3_f32(p.multiply.as_ref(), m[0], m[1], m[2]);
	gl.uniform_3_f32(p.screen.as_ref(), s[0], s[1], s[2]);
	gl.uniform_1_f32(p.opacity.as_ref(), look.opacity);
	gl.uniform_1_i32(p.mask_mode.as_ref(), mask_mode);
	let advanced = blend.is_advanced();
	gl.uniform_1_i32(p.advanced.as_ref(), advanced as i32);
	gl.uniform_1_i32(p.color_blend.as_ref(), blend.color as i32);
	gl.uniform_1_i32(p.alpha_blend.as_ref(), blend.alpha as i32);
	if advanced {
		// The shader works out the result against the copied backdrop.
		gl.disable(glow::BLEND);
		return;
	}
	gl.enable(glow::BLEND);
	match blend.color {
		// Cubism's classic additive and multiplicative blends, as Ayagami
		// draws them (checked there against VTube Studio).
		ColorBlendMode::PremultAdd => gl.blend_func_separate(glow::ONE, glow::ONE, glow::ZERO, glow::ONE),
		ColorBlendMode::PremultMultiply => {
			gl.blend_func_separate(glow::DST_COLOR, glow::ONE_MINUS_SRC_ALPHA, glow::ZERO, glow::ONE)
		}
		_ => {
			debug_assert!(blend.alpha == AlphaBlendMode::Over);
			gl.blend_func_separate(glow::ONE, glow::ONE_MINUS_SRC_ALPHA, glow::ONE, glow::ONE_MINUS_SRC_ALPHA)
		}
	}
}

/// Textures larger than the graphics chip allows are scaled down; texture
/// coordinates run from 0 to 1, so nothing else changes.
fn fit_texture(picture: Picture, max_side: u32) -> Picture {
	if picture.width <= max_side && picture.height <= max_side {
		return picture;
	}
	let k = max_side as f32 / picture.width.max(picture.height) as f32;
	let (w, h) = (((picture.width as f32 * k) as u32).max(1), ((picture.height as f32 * k) as u32).max(1));
	let Some(image) = image::RgbaImage::from_raw(picture.width, picture.height, picture.pixels) else {
		return Picture { width: 1, height: 1, pixels: vec![0; 4] };
	};
	let smaller = image::imageops::resize(&image, w, h, image::imageops::FilterType::Triangle);
	Picture { width: w, height: h, pixels: smaller.into_raw() }
}

/// The model's idle motions (its motion group called "Idle", or any whose name starts with "idle", as
/// Live2D's own player and most models name them), and how many motions each of its groups has.
/// Parameters named by the model's EyeBlink and LipSync groups (or the standard eye and mouth ones)
/// are left to the device, which blinks and speaks itself.
fn load_idle_motions(index: &serde_json::Value, base: &Path, warnings: &mut Vec<String>) -> (Option<IdleMotions>, Vec<(String, usize)>) {
	let mut counts = Vec::new();
	let mut motions = Vec::new();
	let groups = index.get("FileReferences").and_then(|r| r.get("Motions")).and_then(|m| m.as_object());
	for (group, list) in groups.into_iter().flatten() {
		let list = list.as_array().map(Vec::as_slice).unwrap_or(&[]);
		counts.push((group.clone(), list.len()));
		if !group.to_ascii_lowercase().starts_with("idle") {
			continue;
		}
		for item in list {
			let Some(file) = item.get("File").and_then(|f| f.as_str()) else { continue };
			if motions.len() >= MAX_IDLE_MOTIONS {
				warnings.push(format!("only the first {MAX_IDLE_MOTIONS} idle motions are played"));
				break;
			}
			let name = file.rsplit('/').next().unwrap_or(file).trim_end_matches(".motion3.json").to_string();
			let fade = |key: &str| item.get(key).and_then(|x| x.as_f64()).map(|x| x as f32);
			match resolve(base, file)
				.and_then(|p| read_limited(&p))
				.and_then(|b| parse_motion(&name, &b, fade("FadeInTime"), fade("FadeOutTime")))
			{
				Ok(m) => motions.push(m),
				Err(e) => warnings.push(format!("{e}; playing without it")),
			}
		}
	}
	let mut skip: Vec<String> = Vec::new();
	for g in index.get("Groups").and_then(|g| g.as_array()).into_iter().flatten() {
		if matches!(g.get("Name").and_then(|n| n.as_str()), Some("EyeBlink" | "LipSync")) {
			skip.extend(g.get("Ids").and_then(|i| i.as_array()).into_iter().flatten().filter_map(|i| i.as_str().map(str::to_string)));
		}
	}
	for standard in ["ParamEyeLOpen", "ParamEyeROpen", "ParamMouthOpenY"] {
		if !skip.iter().any(|s| s == standard) {
			skip.push(standard.to_string());
		}
	}
	let idle = (!motions.is_empty()).then(|| IdleMotions::new(motions, skip, 1));
	(idle, counts)
}

fn bytes_of<T: Copy>(values: &[T]) -> &[u8] {
	// Only used for f32 and u32 slices, which have no padding.
	unsafe { std::slice::from_raw_parts(values.as_ptr() as *const u8, std::mem::size_of_val(values)) }
}

#[cfg(test)]
mod tests {
	use super::*;

	#[test]
	fn a_pose_file_shows_the_first_part_of_each_group() {
		let pose = br#"{"Type": "Live2D Pose", "FadeInTime": 0.5, "Groups": [
			[{"Id": "PartArmA", "Link": ["PartHandA"]}, {"Id": "PartArmB", "Link": ["PartHandB"]}],
			[{"Id": "PartHatOn", "Link": []}, {"Id": "PartHatOff"}]]}"#;
		let mut groups = PoseGroups::parse(pose).unwrap();
		groups.update(0.016);
		assert_eq!(
			groups.opacities(),
			vec![
				("PartArmA".to_string(), 1.0),
				("PartHandA".to_string(), 1.0),
				("PartArmB".to_string(), 0.0),
				("PartHandB".to_string(), 0.0),
				("PartHatOn".to_string(), 1.0),
				("PartHatOff".to_string(), 0.0)
			]
		);
	}

	#[test]
	fn idle_motions_are_read_from_the_model_description() {
		let dir = std::env::temp_dir().join(format!("stage-idle-{}", std::process::id()));
		std::fs::create_dir_all(dir.join("motions")).unwrap();
		let motion = br#"{"Version": 3, "Meta": {"Duration": 2, "Loop": true}, "Curves": [
			{"Target": "Parameter", "Id": "ParamAngleX", "Segments": [0, 0, 0, 2, 10]}]}"#;
		std::fs::write(dir.join("motions/idle_a.motion3.json"), motion).unwrap();
		std::fs::write(dir.join("motions/tap.motion3.json"), motion).unwrap();
		std::fs::write(dir.join("motions/broken.motion3.json"), b"{").unwrap();
		let index: serde_json::Value = serde_json::from_str(
			r#"{"FileReferences": {"Motions": {
				"Idle": [{"File": "motions/idle_a.motion3.json", "FadeInTime": 0.5}, {"File": "motions/broken.motion3.json"}],
				"TapBody": [{"File": "motions/tap.motion3.json"}],
				"Idle2": [{"File": "../outside.motion3.json"}]}},
			"Groups": [{"Target": "Parameter", "Name": "EyeBlink", "Ids": ["EyeOpenCustom"]}]}"#,
		)
		.unwrap();
		let mut warnings = Vec::new();
		let (idle, groups) = load_idle_motions(&index, &dir, &mut warnings);
		let idle = idle.expect("one idle motion read");
		assert_eq!(idle.names(), vec!["idle_a".to_string()]);
		assert_eq!(warnings.len(), 2, "the broken file and the one outside the folder: {warnings:?}");
		let mut groups = groups;
		groups.sort();
		assert_eq!(groups, vec![("Idle".to_string(), 2), ("Idle2".to_string(), 1), ("TapBody".to_string(), 1)]);
		let (none, _) = load_idle_motions(&serde_json::json!({"FileReferences": {}}), &dir, &mut warnings);
		assert!(none.is_none());
		std::fs::remove_dir_all(&dir).ok();
	}

	#[test]
	fn recognises_model_descriptions() {
		assert!(looks_like_model3(b"  {\"Version\": 3, \"FileReferences\": {\"Moc\": \"a.moc3\"}}"));
		assert!(!looks_like_model3(b"TRNSRTS\0...."));
		assert!(!looks_like_model3(b"{\"nodes\": {}}"));
	}

	#[test]
	fn files_must_stay_inside_the_model_folder() {
		let base = Path::new("/models/alice");
		assert_eq!(resolve(base, "textures/00.png").unwrap(), base.join("textures/00.png"));
		assert_eq!(resolve(base, "./alice.moc3").unwrap(), base.join("./alice.moc3"));
		for bad in ["../secret.png", "/etc/passwd", "a/../../b.png", ""] {
			assert!(resolve(base, bad).is_err(), "{bad}");
		}
	}

	#[test]
	fn textures_are_premultiplied_by_alpha() {
		let mut px = vec![200, 100, 50, 255, 200, 100, 50, 128, 255, 255, 255, 0];
		premultiply(&mut px);
		assert_eq!(px, vec![200, 100, 50, 255, 100, 50, 25, 128, 0, 0, 0, 0]);
	}

	#[test]
	fn oversized_textures_are_scaled_to_fit() {
		let picture = Picture { width: 8, height: 4, pixels: vec![255; 8 * 4 * 4] };
		let fitted = fit_texture(picture, 4);
		assert_eq!((fitted.width, fitted.height), (4, 2));
		assert_eq!(fitted.pixels.len(), 4 * 2 * 4);
	}

	#[test]
	fn expressions_blend_as_cubism_does() {
		let e = parse_expression(
			"Smile",
			br#"{"Type": "Live2D Expression", "Parameters": [
				{"Id": "ParamMouthForm", "Value": 1.0, "Blend": "Add"},
				{"Id": "ParamEyeLOpen", "Value": 0.5, "Blend": "Multiply"},
				{"Id": "ParamCheek", "Value": 1.0, "Blend": "Overwrite"}]}"#,
		)
		.unwrap();
		let mut values = vec![("ParamMouthForm".to_string(), Vec2::new(0.2, 0.0)), ("ParamEyeLOpen".to_string(), Vec2::new(1.0, 0.0))];
		apply_expression(&mut values, |_| 0.0, &e, 0.5);
		let get = |n: &str| values.iter().find(|(k, _)| k == n).unwrap().1.x;
		assert!((get("ParamMouthForm") - 0.7).abs() < 1e-6);
		assert!((get("ParamEyeLOpen") - 0.75).abs() < 1e-6);
		assert!((get("ParamCheek") - 0.5).abs() < 1e-6, "added from its default and blended halfway");
		assert!(parse_expression("Bad", b"{}").is_err());
	}

	#[test]
	fn meshes_sharing_a_mask_share_one_mask_picture() {
		let mut sets = Vec::new();
		assert_eq!(clip_set(&mut sets, &[]), None);
		assert_eq!(clip_set(&mut sets, &[3, 4]), Some(0));
		assert_eq!(clip_set(&mut sets, &[5]), Some(1));
		assert_eq!(clip_set(&mut sets, &[3, 4]), Some(0));
		assert_eq!(sets.len(), 2);
	}
}
