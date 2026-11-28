//! A picture behind the character (the owner's background, chosen on the settings page).
//!
//! Drawn first in each frame, before any kind of character: it fills the screen with no bars (the
//! picture's middle is kept, as a photo is cropped to fill a frame), moves a little the other way as the
//! character turns (a hint of depth), is darkened if asked so the character stands out, and fades from one
//! picture to the next. Set at start from the stage settings, or at once with `{"background": {...}}`.

use std::path::{Path, PathBuf};

use glow::HasContext;
use serde_json::Value;

use crate::overlay::Saved;

/// Seconds to fade from one picture to the next.
const FADE: f32 = 0.6;
/// How far the picture can move at the end of a turn, as a share of the screen.
const PARALLAX: f32 = 0.02;
/// Seconds for the picture to catch up with the character's turn.
const FOLLOW: f32 = 0.4;
/// The largest picture read, on either side.
const LARGEST: u32 = 8192;

#[derive(Clone, Debug, PartialEq)]
pub struct Spec {
	pub path: PathBuf,
	/// How much darker it is drawn: 0 as it is, up to 0.9.
	pub dim: f32,
	/// How much it moves as the character turns: 0 not at all, 1 fully.
	pub parallax: f32,
}

impl Spec {
	/// `{"path": "...", "dim": 0.2, "parallax": 1}`, or null for none.
	pub fn from_json(v: &Value) -> Result<Option<Spec>, String> {
		if v.is_null() {
			return Ok(None);
		}
		let path = v.get("path").and_then(Value::as_str).filter(|p| !p.is_empty()).ok_or("a background needs a \"path\"")?;
		let number = |key: &str, default: f64, top: f64| -> Result<f32, String> {
			match v.get(key) {
				None | Some(Value::Null) => Ok(default as f32),
				Some(x) => x
					.as_f64()
					.filter(|f| (0.0..=top).contains(f))
					.map(|f| f as f32)
					.ok_or(format!("a background's {key} is a number from 0 to {top}")),
			}
		};
		Ok(Some(Spec { path: PathBuf::from(path), dim: number("dim", 0.0, 0.9)?, parallax: number("parallax", 1.0, 1.0)? }))
	}
}

/// The part of a picture (u, v, width, height, in 0..1) that fills a screen with no bars, its middle
/// kept, zoomed by `zoom` (>= 1, room to move) and moved by `shift` (-1..1 on each side, of that room).
pub fn cover(picture: (u32, u32), screen: (u32, u32), zoom: f32, shift: [f32; 2]) -> [f32; 4] {
	let ai = picture.0.max(1) as f32 / picture.1.max(1) as f32;
	let av = screen.0.max(1) as f32 / screen.1.max(1) as f32;
	let (mut w, mut h) = if ai > av { (av / ai, 1.0) } else { (1.0, ai / av) };
	w /= zoom.max(1.0);
	h /= zoom.max(1.0);
	let room = [(1.0 - w) / 2.0, (1.0 - h) / 2.0];
	let u = 0.5 - w / 2.0 + shift[0].clamp(-1.0, 1.0) * room[0];
	let v = 0.5 - h / 2.0 + shift[1].clamp(-1.0, 1.0) * room[1];
	[u, v, w, h]
}

/// A picture file read as RGBA rows (top row first), made smaller first if it is much larger than the
/// screen needs (it only ever fills the screen).
pub fn read_picture(path: &Path, screen: (u32, u32)) -> Result<(u32, u32, Vec<u8>), String> {
	let reader = image::ImageReader::open(path)
		.map_err(|e| format!("cannot open the background {}: {e}", path.display()))?
		.with_guessed_format()
		.map_err(|e| format!("cannot read the background: {e}"))?;
	let (w, h) = reader.into_dimensions().map_err(|e| format!("cannot read the background: {e}"))?;
	if w == 0 || h == 0 || w > LARGEST || h > LARGEST {
		return Err(format!("the background is {w} x {h} pixels; at most {LARGEST} on a side"));
	}
	let picture = image::ImageReader::open(path)
		.map_err(|e| format!("cannot open the background: {e}"))?
		.with_guessed_format()
		.map_err(|e| format!("cannot read the background: {e}"))?
		.decode()
		.map_err(|e| format!("cannot read the background: {e}"))?
		.to_rgba8();
	// The size that covers the screen with a little to spare for moving: no point keeping more.
	let need = |sw: u32, sh: u32| {
		let s = ((sw as f32 * 1.1) / w as f32).max((sh as f32 * 1.1) / h as f32);
		s.min(1.0)
	};
	let s = need(screen.0.max(1), screen.1.max(1));
	let picture = if s < 0.9 {
		let (nw, nh) = (((w as f32 * s).round() as u32).max(1), ((h as f32 * s).round() as u32).max(1));
		image::imageops::resize(&picture, nw, nh, image::imageops::FilterType::Triangle)
	} else {
		picture
	};
	let (pw, ph) = picture.dimensions();
	Ok((pw, ph, picture.into_raw()))
}

struct Layer {
	texture: glow::Texture,
	size: (u32, u32),
	dim: f32,
	parallax: f32,
}

pub struct Background {
	program: glow::Program,
	vao: glow::VertexArray,
	_vbo: glow::Buffer,
	u_uv: Option<glow::UniformLocation>,
	u_shade: Option<glow::UniformLocation>,
	u_alpha: Option<glow::UniformLocation>,
	u_tex: Option<glow::UniformLocation>,
	current: Option<Layer>,
	previous: Option<Layer>,
	/// How far from the previous picture to the current one (0..1).
	fade: f32,
	offset: [f32; 2],
	pub spec: Option<Spec>,
}

const VERTEX: &str = r#"
in vec2 a_pos;
uniform vec4 u_uv;
out vec2 v_uv;
void main() {
	v_uv = u_uv.xy + vec2(a_pos.x, 1.0 - a_pos.y) * u_uv.zw;
	gl_Position = vec4(a_pos * 2.0 - 1.0, 0.0, 1.0);
}
"#;

const FRAGMENT: &str = r#"
in vec2 v_uv;
uniform sampler2D u_tex;
uniform float u_shade;
uniform float u_alpha;
out vec4 o_color;
void main() {
	o_color = vec4(texture(u_tex, v_uv).rgb * u_shade, u_alpha);
}
"#;

impl Background {
	pub fn new(gl: &glow::Context) -> Result<Self, String> {
		unsafe {
			let program = crate::live2d::compile(gl, VERTEX, FRAGMENT)?;
			let vao = gl.create_vertex_array()?;
			let vbo = gl.create_buffer()?;
			let saved_vao = gl.get_parameter_vertex_array(glow::VERTEX_ARRAY_BINDING);
			let saved_buffer = gl.get_parameter_buffer(glow::ARRAY_BUFFER_BINDING);
			gl.bind_vertex_array(Some(vao));
			gl.bind_buffer(glow::ARRAY_BUFFER, Some(vbo));
			let quad: [f32; 8] = [0.0, 0.0, 1.0, 0.0, 0.0, 1.0, 1.0, 1.0];
			let bytes: Vec<u8> = quad.iter().flat_map(|v| v.to_le_bytes()).collect();
			gl.buffer_data_u8_slice(glow::ARRAY_BUFFER, &bytes, glow::STATIC_DRAW);
			let at = gl.get_attrib_location(program, "a_pos").ok_or("the background program has no a_pos")?;
			gl.enable_vertex_attrib_array(at);
			gl.vertex_attrib_pointer_f32(at, 2, glow::FLOAT, false, 8, 0);
			gl.bind_vertex_array(saved_vao);
			gl.bind_buffer(glow::ARRAY_BUFFER, saved_buffer);
			Ok(Self {
				u_uv: gl.get_uniform_location(program, "u_uv"),
				u_shade: gl.get_uniform_location(program, "u_shade"),
				u_alpha: gl.get_uniform_location(program, "u_alpha"),
				u_tex: gl.get_uniform_location(program, "u_tex"),
				program,
				vao,
				_vbo: vbo,
				current: None,
				previous: None,
				fade: 1.0,
				offset: [0.0, 0.0],
				spec: None,
			})
		}
	}

	/// Show this picture (fading from the one shown), or none (fading to the plain colour). A picture
	/// that cannot be read leaves the one shown and says why.
	pub fn set(&mut self, gl: &glow::Context, spec: Option<Spec>, screen: (u32, u32)) -> Result<(), String> {
		if spec == self.spec {
			return Ok(());
		}
		let layer = match &spec {
			None => None,
			Some(s) if Some(&s.path) == self.spec.as_ref().map(|c| &c.path) => {
				// The same picture, only drawn differently: no need to read it again.
				if let Some(c) = self.current.as_mut() {
					c.dim = s.dim;
					c.parallax = s.parallax;
				}
				self.spec = spec;
				return Ok(());
			}
			Some(s) => {
				let (w, h, rgba) = read_picture(&s.path, screen)?;
				let texture = unsafe { upload(gl, w, h, &rgba)? };
				Some(Layer { texture, size: (w, h), dim: s.dim, parallax: s.parallax })
			}
		};
		if let Some(old) = self.previous.take() {
			unsafe { gl.delete_texture(old.texture) };
		}
		self.previous = self.current.take();
		self.current = layer;
		self.fade = 0.0;
		self.spec = spec;
		Ok(())
	}

	/// Whether anything is drawn (a picture, or one fading out).
	pub fn shown(&self) -> bool {
		self.current.is_some() || self.previous.is_some()
	}

	/// Move on by dt seconds: the fade, and the picture following the character's turn (-1..1) and nod.
	pub fn update(&mut self, gl: &glow::Context, dt: f32, turn: f32, nod: f32) {
		if self.fade < 1.0 {
			self.fade = (self.fade + dt / FADE).min(1.0);
			if self.fade >= 1.0 {
				if let Some(old) = self.previous.take() {
					unsafe { gl.delete_texture(old.texture) };
				}
			}
		}
		let k = 1.0 - (-dt / FOLLOW).exp();
		let want = [-turn.clamp(-1.0, 1.0), nod.clamp(-1.0, 1.0) * 0.5];
		for i in 0..2 {
			self.offset[i] += (want[i] - self.offset[i]) * k;
		}
	}

	/// Draw it over the whole of the framebuffer bound now, w x h pixels.
	pub fn draw(&self, gl: &glow::Context, w: u32, h: u32) {
		if !self.shown() {
			return;
		}
		unsafe {
			let saved = Saved::take(gl);
			gl.viewport(0, 0, w as i32, h as i32);
			gl.disable(glow::DEPTH_TEST);
			gl.disable(glow::STENCIL_TEST);
			gl.disable(glow::SCISSOR_TEST);
			gl.disable(glow::CULL_FACE);
			gl.enable(glow::BLEND);
			gl.blend_func_separate(glow::SRC_ALPHA, glow::ONE_MINUS_SRC_ALPHA, glow::ONE, glow::ONE_MINUS_SRC_ALPHA);
			gl.use_program(Some(self.program));
			gl.bind_vertex_array(Some(self.vao));
			gl.active_texture(glow::TEXTURE0);
			gl.uniform_1_i32(self.u_tex.as_ref(), 0);
			let mut layers = Vec::new();
			if let Some(p) = &self.previous {
				layers.push((p, if self.current.is_some() { 1.0 } else { 1.0 - self.fade }));
			}
			if let Some(c) = &self.current {
				layers.push((c, if self.previous.is_some() || self.fade < 1.0 { self.fade } else { 1.0 }));
			}
			for (layer, alpha) in layers {
				if alpha <= 0.0 {
					continue;
				}
				let zoom = 1.0 + 2.0 * PARALLAX * layer.parallax;
				let uv = cover(layer.size, (w, h), zoom, [self.offset[0] * layer.parallax, self.offset[1] * layer.parallax]);
				gl.uniform_4_f32(self.u_uv.as_ref(), uv[0], uv[1], uv[2], uv[3]);
				gl.uniform_1_f32(self.u_shade.as_ref(), 1.0 - layer.dim);
				gl.uniform_1_f32(self.u_alpha.as_ref(), alpha);
				gl.bind_texture(glow::TEXTURE_2D, Some(layer.texture));
				gl.draw_arrays(glow::TRIANGLE_STRIP, 0, 4);
			}
			saved.restore(gl);
		}
	}
}

unsafe fn upload(gl: &glow::Context, w: u32, h: u32, rgba: &[u8]) -> Result<glow::Texture, String> {
	let saved = gl.get_parameter_texture(glow::TEXTURE_BINDING_2D);
	let texture = gl.create_texture()?;
	gl.bind_texture(glow::TEXTURE_2D, Some(texture));
	gl.tex_parameter_i32(glow::TEXTURE_2D, glow::TEXTURE_MIN_FILTER, glow::LINEAR as i32);
	gl.tex_parameter_i32(glow::TEXTURE_2D, glow::TEXTURE_MAG_FILTER, glow::LINEAR as i32);
	gl.tex_parameter_i32(glow::TEXTURE_2D, glow::TEXTURE_WRAP_S, glow::CLAMP_TO_EDGE as i32);
	gl.tex_parameter_i32(glow::TEXTURE_2D, glow::TEXTURE_WRAP_T, glow::CLAMP_TO_EDGE as i32);
	gl.pixel_store_i32(glow::UNPACK_ALIGNMENT, 4);
	gl.tex_image_2d(glow::TEXTURE_2D, 0, glow::RGBA8 as i32, w as i32, h as i32, 0, glow::RGBA, glow::UNSIGNED_BYTE, Some(rgba));
	gl.bind_texture(glow::TEXTURE_2D, saved);
	Ok(texture)
}

#[cfg(test)]
mod tests {
	use super::*;

	#[test]
	fn a_wide_picture_fills_a_tall_screen_from_its_middle() {
		let [u, v, w, h] = cover((1920, 1080), (720, 1280), 1.0, [0.0, 0.0]);
		assert!((h - 1.0).abs() < 1e-6 && (v).abs() < 1e-6);
		assert!((w - (720.0 / 1280.0) / (1920.0 / 1080.0)).abs() < 1e-5);
		assert!((u + w / 2.0 - 0.5).abs() < 1e-6, "centred");
	}

	#[test]
	fn a_tall_picture_fills_a_wide_screen() {
		let [_, v, w, h] = cover((720, 1280), (1280, 720), 1.0, [0.0, 0.0]);
		assert!((w - 1.0).abs() < 1e-6);
		assert!((v + h / 2.0 - 0.5).abs() < 1e-6);
	}

	#[test]
	fn moving_stays_inside_the_picture() {
		for shift in [[-1.0, -1.0], [1.0, 1.0], [5.0, -5.0]] {
			let [u, v, w, h] = cover((1000, 1000), (720, 1280), 1.04, shift);
			assert!(u >= -1e-6 && v >= -1e-6 && u + w <= 1.0 + 1e-6 && v + h <= 1.0 + 1e-6, "{shift:?}");
		}
		let [u0, ..] = cover((1000, 1000), (720, 1280), 1.04, [0.0, 0.0]);
		let [u1, ..] = cover((1000, 1000), (720, 1280), 1.04, [1.0, 0.0]);
		assert!(u1 > u0, "it moves");
	}

	#[test]
	fn the_command_is_read() {
		let s = Spec::from_json(&serde_json::json!({"path": "/x/b.jpg", "dim": 0.3})).unwrap().unwrap();
		assert_eq!(s, Spec { path: "/x/b.jpg".into(), dim: 0.3, parallax: 1.0 });
		assert_eq!(Spec::from_json(&Value::Null).unwrap(), None);
		assert!(Spec::from_json(&serde_json::json!({"path": ""})).is_err());
		assert!(Spec::from_json(&serde_json::json!({"path": "/x", "dim": 2})).is_err());
		assert!(Spec::from_json(&serde_json::json!({"path": "/x", "parallax": -1})).is_err());
	}

	#[test]
	fn large_pictures_are_made_smaller_for_the_screen() {
		let dir = std::env::temp_dir().join(format!("bg-test-{}", std::process::id()));
		std::fs::create_dir_all(&dir).unwrap();
		let path = dir.join("big.png");
		image::RgbaImage::from_pixel(3000, 4000, image::Rgba([10, 20, 30, 255])).save(&path).unwrap();
		let (w, h, rgba) = read_picture(&path, (720, 1280)).unwrap();
		assert!(w < 3000 && h < 4000 && w >= 720 && h >= 1280, "{w} x {h}");
		assert_eq!(rgba.len(), (w * h * 4) as usize);
		assert_eq!(&rgba[..4], &[10, 20, 30, 255]);
		std::fs::remove_dir_all(&dir).ok();
	}
}
