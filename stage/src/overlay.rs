//! Pictures drawn over the character, and the screen dimmed or dark at night.
//!
//! The core draws each picture (captions, the microphone-off sign, a hint while the button is held)
//! as a PNG in the overlay folder, beside the stage's socket (in /run on the device, so nothing is
//! written to the card), and names it in a command:
//!
//! ```text
//! {"overlay": {"slot": "caption", "image": "caption-12.png", "anchor": "bottom"}}
//! {"overlay": {"slot": "caption", "image": null}}
//! {"screen": "dim"}        ("on", "dim" or "off")
//! ```
//!
//! A slot shows one picture at a time: a new one replaces the old at once, and a slot fades in from
//! nothing and out to nothing. Pictures are drawn at their own size in pixels (the core makes them
//! for the screen's size, which the stage reports), made smaller only if they would not fit.
//!
//! Drawing over the character must leave the renderers' state as they left it: Inox2D remembers which
//! program and texture it bound last and does not bind them again, so everything changed here is put
//! back after drawing.

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

use glow::HasContext;

/// Where a picture sits on the screen.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Anchor {
	Top,
	Bottom,
	Center,
	TopLeft,
	TopRight,
	BottomLeft,
	BottomRight,
}

impl Anchor {
	pub fn parse(name: &str) -> Option<Self> {
		Some(match name {
			"top" => Self::Top,
			"bottom" => Self::Bottom,
			"center" | "centre" => Self::Center,
			"top_left" => Self::TopLeft,
			"top_right" => Self::TopRight,
			"bottom_left" => Self::BottomLeft,
			"bottom_right" => Self::BottomRight,
			_ => return None,
		})
	}

	/// The top-left corner of a `w` by `h` picture on a `sw` by `sh` screen, with a margin.
	pub fn place(self, w: f32, h: f32, sw: f32, sh: f32) -> (f32, f32) {
		let m = (sw.min(sh) * 0.03).round();
		let cx = ((sw - w) * 0.5).round();
		let cy = ((sh - h) * 0.5).round();
		match self {
			Self::Top => (cx, m),
			Self::Bottom => (cx, sh - h - m),
			Self::Center => (cx, cy),
			Self::TopLeft => (m, m),
			Self::TopRight => (sw - w - m, m),
			Self::BottomLeft => (m, sh - h - m),
			Self::BottomRight => (sw - w - m, sh - h - m),
		}
	}
}

/// How much of the screen shows.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Screen {
	On,
	/// Dimmed, the character still moving under a dark veil.
	Dim,
	/// Dark: the character is neither moved nor drawn, to save power.
	Off,
}

impl Screen {
	pub fn parse(name: &str) -> Option<Self> {
		match name {
			"on" => Some(Self::On),
			"dim" => Some(Self::Dim),
			"off" => Some(Self::Off),
			_ => None,
		}
	}

	pub fn name(self) -> &'static str {
		match self {
			Self::On => "on",
			Self::Dim => "dim",
			Self::Off => "off",
		}
	}

	/// How dark the veil over everything is.
	fn veil(self) -> f32 {
		match self {
			Self::On => 0.0,
			Self::Dim => DIM_VEIL,
			Self::Off => 1.0,
		}
	}
}

/// How dark "dim" is: the character can still be made out in a dark room.
pub const DIM_VEIL: f32 = 0.72;
const FADE: f32 = 0.18; // seconds for a picture to fade in or out
const VEIL_FADE: f32 = 0.8; // seconds to dim, or to go dark
const WAKE_FADE: f32 = 0.25; // seconds to light up again

/// A picture's name in the overlay folder: plain letters, digits, dashes and dots, ending in .png.
pub fn valid_name(name: &str) -> bool {
	name.len() <= 80
		&& name.ends_with(".png")
		&& !name.starts_with('.')
		&& name.bytes().all(|b| b.is_ascii_alphanumeric() || b"-_.".contains(&b))
}

pub fn valid_slot(name: &str) -> bool {
	!name.is_empty() && name.len() <= 32 && name.bytes().all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b"-_".contains(&b))
}

/// Read a picture from the overlay folder: RGBA, top row first.
pub fn load(folder: &Path, name: &str) -> Result<(u32, u32, Vec<u8>), String> {
	if !valid_name(name) {
		return Err(format!("'{name}' is not a picture name the stage takes"));
	}
	let path: PathBuf = folder.join(name);
	let bytes = std::fs::read(&path).map_err(|e| format!("cannot read {}: {e}", path.display()))?;
	if bytes.len() > 16 * 1024 * 1024 {
		return Err(format!("{name} is too large"));
	}
	let image = image::load_from_memory_with_format(&bytes, image::ImageFormat::Png)
		.map_err(|e| format!("{name} is not a PNG picture: {e}"))?
		.to_rgba8();
	let (w, h) = image.dimensions();
	if w == 0 || h == 0 || w > 4096 || h > 4096 {
		return Err(format!("{name} is {w} by {h} pixels; pictures go up to 4096"));
	}
	Ok((w, h, image.into_raw()))
}

struct Picture {
	texture: glow::Texture,
	width: u32,
	height: u32,
}

struct Slot {
	anchor: Anchor,
	picture: Option<Picture>,
	alpha: f32,
	visible: bool,
}

/// The pictures and the veil, with what they need to be drawn.
pub struct Overlays {
	program: glow::Program,
	vao: glow::VertexArray,
	/// The quad's corners, kept with the vertex array that reads them.
	_vbo: glow::Buffer,
	u_screen: Option<glow::UniformLocation>,
	u_rect: Option<glow::UniformLocation>,
	u_color: Option<glow::UniformLocation>,
	u_textured: Option<glow::UniformLocation>,
	u_tex: Option<glow::UniformLocation>,
	slots: BTreeMap<String, Slot>,
	screen: Screen,
	veil: f32,
}

const VERTEX: &str = r#"
in vec2 a_pos;
uniform vec2 u_screen;
uniform vec4 u_rect;
out vec2 v_uv;
void main() {
	vec2 p = u_rect.xy + a_pos * u_rect.zw;
	v_uv = a_pos;
	gl_Position = vec4(p.x / u_screen.x * 2.0 - 1.0, 1.0 - p.y / u_screen.y * 2.0, 0.0, 1.0);
}
"#;

const FRAGMENT: &str = r#"
in vec2 v_uv;
uniform vec4 u_color;
uniform int u_textured;
uniform sampler2D u_tex;
out vec4 o_color;
void main() {
	vec4 c = u_textured != 0 ? texture(u_tex, v_uv) : vec4(1.0);
	o_color = vec4(c.rgb * u_color.rgb, c.a * u_color.a);
}
"#;

/// GL state the renderers may rely on, saved before drawing over them and put back after.
pub(crate) struct Saved {
	program: Option<glow::Program>,
	vao: Option<glow::VertexArray>,
	array_buffer: Option<glow::Buffer>,
	active_texture: i32,
	texture0: Option<glow::Texture>,
	framebuffer: Option<glow::Framebuffer>,
	viewport: [i32; 4],
	blend: bool,
	blend_func: [i32; 4],
	depth: bool,
	stencil: bool,
	scissor: bool,
	cull: bool,
}

impl Saved {
	pub(crate) unsafe fn take(gl: &glow::Context) -> Self {
		let mut viewport = [0; 4];
		gl.get_parameter_i32_slice(glow::VIEWPORT, &mut viewport);
		let active_texture = gl.get_parameter_i32(glow::ACTIVE_TEXTURE);
		gl.active_texture(glow::TEXTURE0);
		let texture0 = gl.get_parameter_texture(glow::TEXTURE_BINDING_2D);
		Self {
			program: gl.get_parameter_program(glow::CURRENT_PROGRAM),
			vao: gl.get_parameter_vertex_array(glow::VERTEX_ARRAY_BINDING),
			array_buffer: gl.get_parameter_buffer(glow::ARRAY_BUFFER_BINDING),
			active_texture,
			texture0,
			framebuffer: gl.get_parameter_framebuffer(glow::DRAW_FRAMEBUFFER_BINDING),
			viewport,
			blend: gl.is_enabled(glow::BLEND),
			blend_func: [
				gl.get_parameter_i32(glow::BLEND_SRC_RGB),
				gl.get_parameter_i32(glow::BLEND_DST_RGB),
				gl.get_parameter_i32(glow::BLEND_SRC_ALPHA),
				gl.get_parameter_i32(glow::BLEND_DST_ALPHA),
			],
			depth: gl.is_enabled(glow::DEPTH_TEST),
			stencil: gl.is_enabled(glow::STENCIL_TEST),
			scissor: gl.is_enabled(glow::SCISSOR_TEST),
			cull: gl.is_enabled(glow::CULL_FACE),
		}
	}

	pub(crate) unsafe fn restore(&self, gl: &glow::Context) {
		let set = |cap: u32, on: bool| if on { gl.enable(cap) } else { gl.disable(cap) };
		set(glow::BLEND, self.blend);
		set(glow::DEPTH_TEST, self.depth);
		set(glow::STENCIL_TEST, self.stencil);
		set(glow::SCISSOR_TEST, self.scissor);
		set(glow::CULL_FACE, self.cull);
		let [a, b, c, d] = self.blend_func;
		gl.blend_func_separate(a as u32, b as u32, c as u32, d as u32);
		gl.viewport(self.viewport[0], self.viewport[1], self.viewport[2], self.viewport[3]);
		gl.bind_framebuffer(glow::DRAW_FRAMEBUFFER, self.framebuffer);
		gl.active_texture(glow::TEXTURE0);
		gl.bind_texture(glow::TEXTURE_2D, self.texture0);
		gl.active_texture(self.active_texture as u32);
		gl.bind_buffer(glow::ARRAY_BUFFER, self.array_buffer);
		gl.bind_vertex_array(self.vao);
		gl.use_program(self.program);
	}
}

impl Overlays {
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
			let at = gl.get_attrib_location(program, "a_pos").ok_or("the overlay program has no a_pos")?;
			gl.enable_vertex_attrib_array(at);
			gl.vertex_attrib_pointer_f32(at, 2, glow::FLOAT, false, 8, 0);
			gl.bind_vertex_array(saved_vao);
			gl.bind_buffer(glow::ARRAY_BUFFER, saved_buffer);
			Ok(Self {
				u_screen: gl.get_uniform_location(program, "u_screen"),
				u_rect: gl.get_uniform_location(program, "u_rect"),
				u_color: gl.get_uniform_location(program, "u_color"),
				u_textured: gl.get_uniform_location(program, "u_textured"),
				u_tex: gl.get_uniform_location(program, "u_tex"),
				program,
				vao,
				_vbo: vbo,
				slots: BTreeMap::new(),
				screen: Screen::On,
				veil: 0.0,
			})
		}
	}

	/// Show a picture in a slot (replacing the one there).
	pub fn show(&mut self, gl: &glow::Context, slot: &str, anchor: Anchor, (w, h, rgba): (u32, u32, Vec<u8>)) -> Result<(), String> {
		let texture = unsafe {
			let saved = gl.get_parameter_texture(glow::TEXTURE_BINDING_2D);
			let texture = gl.create_texture()?;
			gl.bind_texture(glow::TEXTURE_2D, Some(texture));
			gl.tex_parameter_i32(glow::TEXTURE_2D, glow::TEXTURE_MIN_FILTER, glow::LINEAR as i32);
			gl.tex_parameter_i32(glow::TEXTURE_2D, glow::TEXTURE_MAG_FILTER, glow::LINEAR as i32);
			gl.tex_parameter_i32(glow::TEXTURE_2D, glow::TEXTURE_WRAP_S, glow::CLAMP_TO_EDGE as i32);
			gl.tex_parameter_i32(glow::TEXTURE_2D, glow::TEXTURE_WRAP_T, glow::CLAMP_TO_EDGE as i32);
			gl.pixel_store_i32(glow::UNPACK_ALIGNMENT, 4);
			gl.tex_image_2d(glow::TEXTURE_2D, 0, glow::RGBA8 as i32, w as i32, h as i32, 0, glow::RGBA, glow::UNSIGNED_BYTE, Some(&rgba));
			gl.bind_texture(glow::TEXTURE_2D, saved);
			texture
		};
		let entry = self.slots.entry(slot.to_string()).or_insert(Slot { anchor, picture: None, alpha: 0.0, visible: false });
		if let Some(old) = entry.picture.take() {
			unsafe { gl.delete_texture(old.texture) };
		}
		entry.anchor = anchor;
		entry.picture = Some(Picture { texture, width: w, height: h });
		entry.visible = true;
		Ok(())
	}

	/// Fade a slot's picture out.
	pub fn hide(&mut self, slot: &str) {
		if let Some(entry) = self.slots.get_mut(slot) {
			entry.visible = false;
		}
	}

	pub fn set_screen(&mut self, screen: Screen) {
		self.screen = screen;
	}

	pub fn screen(&self) -> Screen {
		self.screen
	}

	/// The screen is dark: the character need not be moved or drawn.
	pub fn dark(&self) -> bool {
		self.screen == Screen::Off && self.veil >= 0.999
	}

	/// Which slots show a picture now (for the heartbeat and tests).
	pub fn showing(&self) -> Vec<String> {
		self.slots.iter().filter(|(_, s)| s.visible && s.picture.is_some()).map(|(n, _)| n.clone()).collect()
	}

	/// Move the fades on by `dt` seconds.
	pub fn update(&mut self, gl: &glow::Context, dt: f32) {
		let target = self.screen.veil();
		let speed = if target < self.veil { 1.0 / WAKE_FADE } else { 1.0 / VEIL_FADE };
		self.veil = step(self.veil, target, speed * dt);
		for slot in self.slots.values_mut() {
			slot.alpha = step(slot.alpha, if slot.visible { 1.0 } else { 0.0 }, dt / FADE);
			if !slot.visible && slot.alpha <= 0.0 {
				if let Some(old) = slot.picture.take() {
					unsafe { gl.delete_texture(old.texture) };
				}
			}
		}
	}

	/// Draw the pictures over whatever is on the screen, then the veil (pictures stay under the veil:
	/// the screen dims as a whole). Does nothing, and costs nothing, when nothing shows.
	pub fn draw(&self, gl: &glow::Context, width: u32, height: u32) {
		let pictures: Vec<&Slot> = self.slots.values().filter(|s| s.alpha > 0.0 && s.picture.is_some()).collect();
		if pictures.is_empty() && self.veil <= 0.0 {
			return;
		}
		let (sw, sh) = (width.max(1) as f32, height.max(1) as f32);
		unsafe {
			let saved = Saved::take(gl);
			gl.bind_framebuffer(glow::DRAW_FRAMEBUFFER, None);
			gl.viewport(0, 0, width as i32, height as i32);
			gl.disable(glow::DEPTH_TEST);
			gl.disable(glow::STENCIL_TEST);
			gl.disable(glow::SCISSOR_TEST);
			gl.disable(glow::CULL_FACE);
			gl.enable(glow::BLEND);
			gl.blend_func_separate(glow::SRC_ALPHA, glow::ONE_MINUS_SRC_ALPHA, glow::ONE, glow::ONE_MINUS_SRC_ALPHA);
			gl.use_program(Some(self.program));
			gl.bind_vertex_array(Some(self.vao));
			gl.uniform_2_f32(self.u_screen.as_ref(), sw, sh);
			gl.active_texture(glow::TEXTURE0);
			gl.uniform_1_i32(self.u_tex.as_ref(), 0);
			for slot in pictures {
				let picture = slot.picture.as_ref().unwrap();
				// Drawn at its own size; smaller only if it would not fit.
				let fit = (sw * 0.96 / picture.width as f32).min(sh * 0.96 / picture.height as f32).min(1.0);
				let (w, h) = (picture.width as f32 * fit, picture.height as f32 * fit);
				let (x, y) = slot.anchor.place(w, h, sw, sh);
				gl.bind_texture(glow::TEXTURE_2D, Some(picture.texture));
				gl.uniform_1_i32(self.u_textured.as_ref(), 1);
				gl.uniform_4_f32(self.u_color.as_ref(), 1.0, 1.0, 1.0, slot.alpha);
				gl.uniform_4_f32(self.u_rect.as_ref(), x, y, w, h);
				gl.draw_arrays(glow::TRIANGLE_STRIP, 0, 4);
			}
			if self.veil > 0.0 {
				gl.uniform_1_i32(self.u_textured.as_ref(), 0);
				gl.uniform_4_f32(self.u_color.as_ref(), 0.0, 0.0, 0.0, self.veil);
				gl.uniform_4_f32(self.u_rect.as_ref(), 0.0, 0.0, sw, sh);
				gl.draw_arrays(glow::TRIANGLE_STRIP, 0, 4);
			}
			saved.restore(gl);
		}
	}
}

fn step(value: f32, target: f32, by: f32) -> f32 {
	if value < target {
		(value + by).min(target)
	} else {
		(value - by).max(target)
	}
}

#[cfg(test)]
mod tests {
	use super::*;

	#[test]
	fn only_plain_picture_names_are_taken() {
		for good in ["caption-12.png", "mic_off-1.png", "hint.png"] {
			assert!(valid_name(good), "{good}");
		}
		for bad in ["../secrets.png", "/etc/x.png", "a/b.png", ".hidden.png", "caption.jpg", "x.png\n", ""] {
			assert!(!valid_name(bad), "{bad:?}");
		}
		assert!(valid_slot("caption") && valid_slot("mic-off") && !valid_slot("Caption") && !valid_slot(""));
	}

	#[test]
	fn pictures_sit_where_their_anchor_says() {
		let (sw, sh) = (720.0, 1280.0);
		let (x, y) = Anchor::Bottom.place(600.0, 200.0, sw, sh);
		assert_eq!((x, y), (60.0, 1280.0 - 200.0 - 22.0));
		assert_eq!(Anchor::Top.place(600.0, 100.0, sw, sh), (60.0, 22.0));
		assert_eq!(Anchor::TopRight.place(96.0, 96.0, sw, sh), (720.0 - 96.0 - 22.0, 22.0));
		assert_eq!(Anchor::Center.place(100.0, 100.0, sw, sh), (310.0, 590.0));
		assert_eq!(Anchor::parse("bottom"), Some(Anchor::Bottom));
		assert_eq!(Anchor::parse("left"), None);
	}

	#[test]
	fn fades_move_toward_their_targets_and_stop_there() {
		assert_eq!(step(0.0, 1.0, 0.3), 0.3);
		assert_eq!(step(0.9, 1.0, 0.3), 1.0);
		assert_eq!(step(0.2, 0.0, 0.3), 0.0);
		assert_eq!(Screen::Dim.veil(), DIM_VEIL);
		assert_eq!(Screen::parse("off"), Some(Screen::Off));
		assert_eq!(Screen::parse("sleep"), None);
	}

	#[test]
	fn a_picture_is_read_from_the_overlay_folder_only() {
		let dir = std::env::temp_dir().join(format!("overlay-test-{}", std::process::id()));
		std::fs::create_dir_all(&dir).unwrap();
		let mut png = Vec::new();
		image::RgbaImage::from_pixel(3, 2, image::Rgba([255, 0, 0, 128]))
			.write_to(&mut std::io::Cursor::new(&mut png), image::ImageFormat::Png)
			.unwrap();
		std::fs::write(dir.join("caption-1.png"), &png).unwrap();
		let (w, h, rgba) = load(&dir, "caption-1.png").unwrap();
		assert_eq!((w, h, rgba.len()), (3, 2, 24));
		assert_eq!(&rgba[..4], &[255, 0, 0, 128]);
		assert!(load(&dir, "../caption-1.png").is_err());
		assert!(load(&dir, "missing.png").is_err());
		std::fs::write(dir.join("junk.png"), b"not a png").unwrap();
		assert!(load(&dir, "junk.png").is_err());
		std::fs::remove_dir_all(&dir).unwrap();
	}
}
