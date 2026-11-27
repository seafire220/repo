//! The stage: draws and animates the character on the device screen.
//!
//! It loads a character (an Inochi2D puppet, a Live2D-format model played
//! with Ayagami, or a VRM 3D avatar), keeps it alive with breathing, blinking and small movements,
//! and moves its mouth and changes its manner according to commands from the
//! core service. It holds no API keys and makes no network connections.

mod behaviour;
mod capture;
mod control;
mod director;
mod live;
mod live2d;
mod live2d_motion;
mod mapping;
mod motion;
mod overlay;
mod overrides;
mod puppet_info;
mod smooth;
mod speech;
mod vrm;
mod window;

use std::path::PathBuf;
use std::sync::mpsc::{channel, Receiver};
use std::time::Instant;

use glam::Vec2;
use glow::HasContext;
use inox2d::formats::inp::parse_inp;
use inox2d::model::Model;
use inox2d::render::InoxRendererExt;
use inox2d_opengl::OpenglRenderer;
use live2d::{Live2dPuppet, Live2dRenderer};
use serde_json::json;
use winit::window::{Fullscreen, WindowBuilder};

use behaviour::{Behaviour, State};
use control::{Command, Events, Script};
use mapping::Mapper;
use motion::{Library, Motion, Start};
use overrides::Overrides;
use puppet_info::PuppetInfo;
use vrm::anim::{self, Scene};
use vrm::render::{Shot, VrmRenderer};
use vrm::{Profile, VrmPuppet};
use window::{App, AppFrame, GlPreference};

const USAGE: &str = "\
Usage: stage CHARACTER [options]

  CHARACTER is an Inochi2D puppet (.inp or .inx), a Live2D-format model
  (.model3.json, with the files it names beside it) or a VRM avatar (.vrm).
  The kind is decided by the file's contents, so a link with any name works.

  --size WxH            window size when not full screen (default 720x1280)
  --fullscreen          take the whole screen
  --gl auto|es|desktop  which OpenGL to ask for (default auto: ES first)
  --map FILE            JSON file overriding which parameters the controls drive
                        (a missing file, or one this character cannot use, means
                        the automatic choice; the second is reported as a warning)
  --socket PATH         also accept commands and send events on this local socket
  --overlays DIR        the folder of pictures the core asks to show over the character
                        (captions, the microphone-off sign); default: \"overlays\" beside
                        the socket
  --no-stdin            do not read commands from standard input
  --background RRGGBB   background colour (default 101014)
  --seed N              seed for blink timing (default 1)
  --still               no idle movement; only commanded mouth, blink and look
  --view X0,Y0,X1,Y1    show this box instead of the whole character (puppet units;
                        canvas pixels for Live2D-format models)
  --shot bust|upper|full  how much of a VRM avatar to show (default upper)
  --profile auto|l1|l2  memory profile for VRM avatars: textures up to 1024 (l1)
                        or 2048 (l2) pixels a side (default auto: l2 with 1.5 GB
                        of memory or more)
  --vrm-animation light|full
                        how a VRM avatar moves: light (the same controls as the 2D
                        characters, the default) or full (animation clips on the
                        whole skeleton, with the controls on top)
  --clips DIR           a folder of VRM Animation clips (.vrma) for the full style;
                        give it again for more (a later folder's clip replaces an
                        earlier one of the same name). Default: the built-in clips
  --quality auto|normal|lite
                        how much a VRM avatar's drawing may cost: lite leaves out
                        outlines and keeps textures small; auto (the default)
                        steps down by itself if frames come too slowly
  --model-motions F     how much of a Live2D-format model's own idle motions shows,
                        0 (off) to 1 (as its artist made them; the default)
  --settings FILE       JSON file with any of: vrm_animation, clip_folders (builtin
                        names the built-in clips), quality, shot, model_motions
                        (written by the core for the character on the screen; the
                        options above win; a missing file is fine)

Testing:
  --script FILE         timed commands, one JSON object per line with \"at\"
  --fixed-dt SECONDS    advance time by a fixed step per frame
  --capture-dir DIR     where captured frames are written
  --capture-at T,T,...  times at which to save a frame
  --exit-after SECONDS  stop after this much stage time
  --no-physics          hair and clothes do not swing (Live2D-format models and VRM avatars)
  --draw-scale F        draw a VRM avatar at this fraction of the resolution, scaled up
                        (what a slow device steps down to)
";

struct Options {
	puppet: PathBuf,
	width: u32,
	height: u32,
	fullscreen: bool,
	gl: GlPreference,
	map: Option<PathBuf>,
	socket: Option<PathBuf>,
	overlays: Option<PathBuf>,
	stdin: bool,
	background: [f32; 3],
	seed: u64,
	still: bool,
	physics: bool,
	view: Option<[f32; 4]>,
	shot: Shot,
	shot_given: bool,
	profile: Option<Profile>,
	vrm_animation: Option<VrmStyle>,
	clip_folders: Vec<PathBuf>,
	quality: Option<Quality>,
	/// How much of a Live2D-format model's own idle motions shows (0 to 1).
	model_motions: Option<f32>,
	settings: Option<PathBuf>,
	draw_scale: Option<f32>,
	script: Option<PathBuf>,
	fixed_dt: Option<f32>,
	capture_dir: Option<PathBuf>,
	capture_at: Vec<f32>,
	exit_after: Option<f32>,
}

fn parse_options(args: &[String]) -> Result<Options, String> {
	let mut o = Options {
		puppet: PathBuf::new(),
		width: 720,
		height: 1280,
		fullscreen: false,
		gl: GlPreference::Auto,
		map: None,
		socket: None,
		overlays: None,
		stdin: true,
		background: [0x10 as f32 / 255.0, 0x10 as f32 / 255.0, 0x14 as f32 / 255.0],
		seed: 1,
		still: false,
		physics: true,
		view: None,
		shot: Shot::Upper,
		shot_given: false,
		profile: None,
		vrm_animation: None,
		clip_folders: Vec::new(),
		quality: None,
		model_motions: None,
		settings: None,
		draw_scale: None,
		script: None,
		fixed_dt: None,
		capture_dir: None,
		capture_at: Vec::new(),
		exit_after: None,
	};
	let mut puppet = None;
	let mut it = args.iter();
	while let Some(arg) = it.next() {
		let mut value = |name: &str| it.next().cloned().ok_or(format!("{name} needs a value"));
		match arg.as_str() {
			"--size" => {
				let v = value("--size")?;
				let (w, h) = v.split_once('x').ok_or("--size must look like 720x1280")?;
				o.width = w.parse().map_err(|_| "--size width is not a number")?;
				o.height = h.parse().map_err(|_| "--size height is not a number")?;
				if o.width == 0 || o.height == 0 {
					return Err("--size must be larger than zero".into());
				}
			}
			"--fullscreen" => o.fullscreen = true,
			"--gl" => {
				o.gl = match value("--gl")?.as_str() {
					"auto" => GlPreference::Auto,
					"es" => GlPreference::Es,
					"desktop" => GlPreference::Desktop,
					other => return Err(format!("--gl must be auto, es or desktop, not '{other}'")),
				}
			}
			"--map" => o.map = Some(value("--map")?.into()),
			"--socket" => o.socket = Some(value("--socket")?.into()),
			"--overlays" => o.overlays = Some(value("--overlays")?.into()),
			"--no-stdin" => o.stdin = false,
			"--background" => {
				let v = value("--background")?;
				let n = u32::from_str_radix(&v, 16).ok().filter(|_| v.len() == 6).ok_or("--background must be RRGGBB")?;
				o.background = [(n >> 16 & 255) as f32 / 255.0, (n >> 8 & 255) as f32 / 255.0, (n & 255) as f32 / 255.0];
			}
			"--seed" => o.seed = value("--seed")?.parse().map_err(|_| "--seed is not a number")?,
			"--still" => o.still = true,
			"--no-physics" => o.physics = false,
			"--view" => {
				let v: Vec<f32> = value("--view")?
					.split(',')
					.map(|t| t.trim().parse::<f32>())
					.collect::<Result<_, _>>()
					.map_err(|_| "--view must be four numbers: X0,Y0,X1,Y1")?;
				if v.len() != 4 || v[2] <= v[0] || v[3] <= v[1] {
					return Err("--view must be four numbers X0,Y0,X1,Y1 with X1 > X0 and Y1 > Y0".into());
				}
				o.view = Some([v[0], v[1], v[2], v[3]]);
			}
			"--shot" => {
				let v = value("--shot")?;
				o.shot = Shot::parse(&v).ok_or(format!("--shot must be bust, upper or full, not '{v}'"))?;
				o.shot_given = true;
			}
			"--vrm-animation" => {
				let v = value("--vrm-animation")?;
				o.vrm_animation = Some(VrmStyle::parse(&v).ok_or(format!("--vrm-animation must be light or full, not '{v}'"))?);
			}
			"--clips" => o.clip_folders.push(value("--clips")?.into()),
			"--quality" => {
				let v = value("--quality")?;
				o.quality = Some(Quality::parse(&v).ok_or(format!("--quality must be auto, normal or lite, not '{v}'"))?);
			}
			"--settings" => o.settings = Some(value("--settings")?.into()),
			"--model-motions" => {
				let v: f32 = value("--model-motions")?.parse().map_err(|_| "--model-motions is not a number")?;
				if !(0.0..=1.0).contains(&v) {
					return Err("--model-motions must be between 0 and 1".into());
				}
				o.model_motions = Some(v);
			}
			"--draw-scale" => {
				let v: f32 = value("--draw-scale")?.parse().map_err(|_| "--draw-scale is not a number")?;
				if !(0.25..=1.0).contains(&v) {
					return Err("--draw-scale must be between 0.25 and 1".into());
				}
				o.draw_scale = Some(v);
			}
			"--profile" => {
				o.profile = match value("--profile")?.as_str() {
					"auto" => None,
					"l1" => Some(Profile::L1),
					"l2" => Some(Profile::L2),
					other => return Err(format!("--profile must be auto, l1 or l2, not '{other}'")),
				}
			}
			"--script" => o.script = Some(value("--script")?.into()),
			"--fixed-dt" => {
				let dt: f32 = value("--fixed-dt")?.parse().map_err(|_| "--fixed-dt is not a number")?;
				if !(dt > 0.0 && dt <= 0.25) {
					return Err("--fixed-dt must be between 0 and 0.25".into());
				}
				o.fixed_dt = Some(dt);
			}
			"--capture-dir" => o.capture_dir = Some(value("--capture-dir")?.into()),
			"--capture-at" => {
				for part in value("--capture-at")?.split(',') {
					o.capture_at.push(part.trim().parse().map_err(|_| format!("--capture-at: '{part}' is not a number"))?);
				}
				o.capture_at.sort_by(|a, b| a.total_cmp(b));
			}
			"--exit-after" => o.exit_after = Some(value("--exit-after")?.parse().map_err(|_| "--exit-after is not a number")?),
			"-h" | "--help" => return Err(String::new()),
			other if other.starts_with("--") => return Err(format!("unknown option {other}")),
			other => {
				if puppet.replace(PathBuf::from(other)).is_some() {
					return Err("only one puppet file can be given".into());
				}
			}
		}
	}
	o.puppet = puppet.ok_or("no puppet file given")?;
	if o.overlays.is_none() {
		o.overlays = o.socket.as_ref().and_then(|s| s.parent()).map(|d| d.join("overlays"));
	}
	if !o.capture_at.is_empty() && o.capture_dir.is_none() {
		return Err("--capture-at needs --capture-dir".into());
	}
	Ok(o)
}

/// How a VRM avatar moves.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum VrmStyle {
	/// The controls the 2D characters use, turned into bone turns (light on memory).
	Light,
	/// Animation clips on the whole skeleton, with the controls on top.
	Full,
}

impl VrmStyle {
	fn parse(s: &str) -> Option<VrmStyle> {
		match s {
			"light" => Some(VrmStyle::Light),
			"full" => Some(VrmStyle::Full),
			_ => None,
		}
	}
}

/// How much a VRM avatar's drawing may cost.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum Quality {
	/// Normal, stepping down by itself if frames come too slowly.
	Auto,
	Normal,
	/// No outlines, small textures, springs at half rate.
	Lite,
}

impl Quality {
	fn parse(s: &str) -> Option<Quality> {
		match s {
			"auto" => Some(Quality::Auto),
			"normal" => Some(Quality::Normal),
			"lite" => Some(Quality::Lite),
			_ => None,
		}
	}
}

/// The settings file the core writes for the character on the screen. Options given on the command
/// line win; anything missing or unreadable is left at its default (with a warning), never stopping
/// the character from showing.
fn read_settings(o: &mut Options) -> Option<String> {
	let path = o.settings.clone()?;
	if !path.exists() {
		return None;
	}
	let text = match std::fs::read_to_string(&path) {
		Ok(t) => t,
		Err(e) => return Some(format!("the stage settings were not read: {e}")),
	};
	let v: serde_json::Value = match serde_json::from_str(&text) {
		Ok(v) => v,
		Err(e) => return Some(format!("the stage settings were not used: {e}")),
	};
	let mut problems = Vec::new();
	let text_of = |key: &str| v.get(key).and_then(serde_json::Value::as_str);
	if o.vrm_animation.is_none() {
		if let Some(s) = text_of("vrm_animation") {
			o.vrm_animation = VrmStyle::parse(s);
			if o.vrm_animation.is_none() {
				problems.push(format!("vrm_animation '{s}'"));
			}
		}
	}
	if o.clip_folders.is_empty() {
		if let Some(list) = v.get("clip_folders").and_then(serde_json::Value::as_array) {
			o.clip_folders = list.iter().filter_map(|f| f.as_str()).take(8).map(PathBuf::from).collect();
		}
	}
	if o.quality.is_none() {
		if let Some(s) = text_of("quality") {
			o.quality = Quality::parse(s);
			if o.quality.is_none() {
				problems.push(format!("quality '{s}'"));
			}
		}
	}
	if o.model_motions.is_none() {
		if let Some(x) = v.get("model_motions") {
			match x.as_f64().filter(|f| (0.0..=1.0).contains(f)) {
				Some(f) => o.model_motions = Some(f as f32),
				None => problems.push(format!("model_motions {x}")),
			}
		}
	}
	if !o.shot_given {
		if let Some(s) = text_of("shot") {
			match Shot::parse(s) {
				Some(shot) => o.shot = shot,
				None => problems.push(format!("shot '{s}'")),
			}
		}
	}
	(!problems.is_empty()).then(|| format!("the stage settings had values it does not know: {}", problems.join(", ")))
}

/// Where the built-in clips are: installed on the device, or beside the source when testing.
fn builtin_clip_folder() -> Option<PathBuf> {
	let mut candidates = vec![PathBuf::from("/opt/companion/share/vrma")];
	if let Ok(exe) = std::env::current_exe() {
		// stage/target/release/stage -> stage/motions/vrma
		if let Some(stage_dir) = exe.parent().and_then(|p| p.parent()).and_then(|p| p.parent()) {
			candidates.push(stage_dir.join("motions/vrma"));
		}
	}
	candidates.push(PathBuf::from(concat!(env!("CARGO_MANIFEST_DIR"), "/motions/vrma")));
	candidates.into_iter().find(|p| p.is_dir())
}

/// What a VRM avatar's drawing gives up, one step at a time, when frames come too slowly.
const QUALITY_STEPS: [&str; 4] =
	["outlines left out", "hair and clothes moved at half rate", "drawn at three-quarters resolution", "drawn at half resolution"];
/// Lite starts with the first two steps taken.
const LITE_LEVEL: usize = 2;

/// Watches how fast frames come, in real time, and says when to take the next step down.
struct Watchdog {
	level: usize,
	frames: u32,
	window: f32,
	slow: u32,
}

impl Watchdog {
	/// Frames are judged over this many seconds, after the first few (loading, settling).
	const WINDOW: f32 = 3.0;
	const WARM_UP: f32 = 8.0;
	/// Fewer frames a second than this, twice running, takes a step down.
	const MIN_FPS: f32 = 20.0;

	fn new(level: usize) -> Self {
		Watchdog { level, frames: 0, window: 0.0, slow: 0 }
	}

	/// A frame was drawn, `real` seconds after the one before, `t` seconds into the stage's life.
	/// Returns the step to take now, if any, and the frame rate that called for it.
	fn frame(&mut self, real: f32, t: f32) -> Option<(usize, f32)> {
		self.frames += 1;
		self.window += real.max(0.0);
		if self.window < Self::WINDOW {
			return None;
		}
		let fps = self.frames as f32 / self.window;
		self.frames = 0;
		self.window = 0.0;
		if t < Self::WARM_UP {
			return None;
		}
		self.slow = if fps < Self::MIN_FPS { self.slow + 1 } else { 0 };
		if self.slow >= 2 && self.level < QUALITY_STEPS.len() {
			self.slow = 0;
			self.level += 1;
			return Some((self.level, fps));
		}
		None
	}
}

/// How much of a part of the body a gesture holding it takes back from a model's own idle motion.
const OWN_MOTION_GIVE_WAY: f32 = 0.8;

/// Controls whose parameters a model's own motions leave to the device: blinks, the mouth opening and
/// the mouth shapes of speech.
fn own_motion_left_alone(c: mapping::Control) -> bool {
	use mapping::Control::*;
	matches!(c, BlinkLeft | BlinkRight | MouthOpen | VowelA | VowelI | VowelU | VowelE | VowelO)
}

/// The part of the body (a motion system channel) a control moves.
fn control_channel(c: mapping::Control) -> Option<usize> {
	use mapping::Control::*;
	let name = match c {
		HeadYaw | HeadPitch | HeadRoll | HeadTurn => "head",
		BodyYaw | BodyPitch | BodyRoll | Shoulders => "torso",
		GazeX | GazeY => "eyes",
		BrowLeft | BrowRight | BrowFormLeft | BrowFormRight => "brows",
		Smile | EyeSmileLeft | EyeSmileRight | Cheek => "expression",
		ArmLeft | ArmLeftB => "arm_left",
		ArmRight | ArmRightB => "arm_right",
		MouthOpen | VowelA | VowelI | VowelU | VowelE | VowelO => "mouth",
		BlinkLeft | BlinkRight => "eyelids",
		Breath => return None,
	};
	motion::CHANNELS.iter().position(|n| *n == name)
}

/// The gestures an avatar plays from its own clips, for the motion system.
fn clip_gestures(puppet: &VrmPuppet) -> Vec<(String, motion::External)> {
	let Some(a) = puppet.animator() else { return Vec::new() };
	a.one_offs()
		.map(|c| {
			let e = motion::External {
				duration: c.duration.max(0.5),
				touches: c.touches,
				holds: c.holds,
				stroke: c.stroke,
				replaces: c.touches & anim::BODY,
			};
			(c.name.clone(), e)
		})
		.collect()
}

/// The character, in whichever format it came.
enum Puppet {
	Inox(Model),
	Live2d(Box<Live2dPuppet>),
	Vrm(Box<VrmPuppet>),
}

impl Puppet {
	fn format(&self) -> &'static str {
		match self {
			Puppet::Inox(_) => "inochi2d",
			Puppet::Live2d(_) => "live2d",
			Puppet::Vrm(_) => "vrm",
		}
	}
}

enum Renderer {
	Inox(OpenglRenderer),
	Live2d(Box<Live2dRenderer>),
	Vrm(Box<VrmRenderer>),
}

struct StageApp {
	opts: Options,
	puppet: Puppet,
	info: PuppetInfo,
	mapper: Mapper,
	behaviour: Behaviour,
	motion: Motion,
	/// The sentence being said: its mouth shapes and the movements tied to its words.
	speech: speech::Speech,
	director: director::Director,
	/// The model's own expressions and how much each shows (Live2D-format models).
	expressions: Vec<live2d::Expression>,
	expression_weights: Vec<(smooth::Spring, f32)>,
	overrides: Overrides,
	/// A person's movement, live (live.rs).
	live: live::Live,
	commands: Receiver<Command>,
	script: Option<Script>,
	events: Events,
	on_window: Option<(Renderer, glow::Context)>,
	width: u32,
	height: u32,
	t: f32,
	last: Instant,
	frames_since_beat: u32,
	last_beat: Instant,
	/// Seconds spent since the last heartbeat posing the character and issuing its drawing.
	update_time: f64,
	draw_time: f64,
	next_capture: usize,
	quit: bool,
	/// Why the owner's movement settings were not used, told once the stage is ready.
	map_warning: Option<String>,
	/// Other problems to tell once the stage is ready (settings, animation clips).
	warnings: Vec<String>,
	/// A VRM avatar's drawing, stepping down if frames come too slowly (None: not watched).
	watchdog: Option<Watchdog>,
	last_real: Instant,
	/// Pictures over the character, and the screen's brightness (overlay.rs).
	overlays: Option<overlay::Overlays>,
	/// A Live2D-format model's own idle motions: how much shows (the owner's choice), how much of that
	/// shows now (less while listening, talking or dozing), and the part of the body each parameter
	/// belongs to (a gesture holding that part takes it back from the motion).
	own_strength: f32,
	own_share: smooth::Spring,
	own_channels: std::collections::HashMap<String, usize>,
}

impl StageApp {
	/// Scale and centre the puppet so all of it is visible.
	fn fit_camera(&mut self) {
		let Some((renderer, _)) = &mut self.on_window else {
			return;
		};
		let (w, h) = (self.width.max(1) as f32, self.height.max(1) as f32);
		let (scale, centre) = match self.opts.view.or(self.info.bounds) {
			Some([x0, y0, x1, y1]) if x1 > x0 && y1 > y0 => {
				(0.94 * (w / (x1 - x0)).min(h / (y1 - y0)), Vec2::new((x0 + x1) * 0.5, (y0 + y1) * 0.5))
			}
			_ => (0.15, Vec2::ZERO),
		};
		match renderer {
			Renderer::Inox(r) => {
				r.camera.scale = Vec2::splat(scale);
				r.camera.position = -centre;
			}
			Renderer::Live2d(r) => r.camera = live2d::Camera { centre, scale },
			// A VRM avatar frames itself (its head, hips and hands), by the shot asked for.
			Renderer::Vrm(_) => {}
		}
	}

	/// What the character can do now (after the library changed), and with `clips` the whole library.
	fn announce_library(&self, clips: bool) {
		let mut event = json!({
			"event": "library",
			"gestures": self.motion.gestures(),
			"actions": self.motion.actions(),
			"strokes": self
				.motion
				.strokes()
				.into_iter()
				.map(|(name, at)| (name, json!(at)))
				.collect::<serde_json::Map<String, serde_json::Value>>(),
		});
		if clips {
			event["clips"] = self.motion.describe();
			event["controls"] = json!(motion::KEYS);
		}
		self.events.emit(event);
	}

	/// Play the director's reactions (looking up, waking, greeting).
	fn react(&mut self, reactions: &[&'static str]) {
		for name in reactions {
			if self.motion.react(name) {
				self.events.emit(json!({"event": "gesture", "name": name, "cause": "reaction"}));
			}
		}
	}

	fn handle(&mut self, command: Command) {
		match command {
			Command::Mouth(level) => self.behaviour.set_mouth(level),
			Command::State(state) => {
				if state != State::Speaking {
					self.speech.stop();
				}
				if state != self.behaviour.state() {
					self.behaviour.set_state(state);
					self.events.emit(json!({"event": "state", "state": state.name()}));
					let reactions = self.director.on_state(state);
					self.react(&reactions);
				}
			}
			Command::Clock(hour) => self.director.set_clock(hour),
			Command::Poke => {
				let reactions = self.director.poke();
				self.react(&reactions);
			}
			Command::Heard(level) => self.director.hear(level),
			Command::Temperament(energy, expressiveness) => {
				self.director.set_temperament(energy, expressiveness);
				self.motion.set_expressiveness(expressiveness);
			}
			Command::GesturesOff(names) => self.motion.set_disabled(&names),
			Command::Define(entry) => match self.motion.define(&entry) {
				Ok(_) => self.announce_library(false),
				Err(e) => self.events.emit(json!({"event": "error", "message": e})),
			},
			Command::Undefine(name) => match self.motion.undefine(&name) {
				Ok(()) => self.announce_library(false),
				Err(e) => self.events.emit(json!({"event": "error", "message": e})),
			},
			Command::Describe => self.announce_library(true),
			Command::Look(x, y) => self.behaviour.set_look(x, y),
			Command::Blink => self.behaviour.blink_now(),
			Command::Set(name, value, ease) => {
				if !self.overrides.set(&name, value, ease) {
					self.events.emit(json!({"event": "error", "message": format!("this puppet has no parameter '{name}'")}));
				}
			}
			Command::Unset(name, ease) => self.overrides.clear(&name, ease),
			Command::ClearAll(ease) => self.overrides.clear_all(ease),
			Command::Emotion(name, intensity) => match self.motion.set_emotion(&name, intensity) {
				Ok(started) => {
					self.events.emit(json!({"event": "emotion", "emotion": name, "intensity": intensity}));
					if let Some(g) = started {
						self.events.emit(json!({"event": "gesture", "name": g, "cause": "emotion"}));
					}
				}
				Err(e) => self.events.emit(json!({"event": "error", "message": e})),
			},
			Command::Gesture(name, intensity) => match self.motion.play(&name, intensity) {
				Ok(Start::Now) => self.events.emit(json!({"event": "gesture", "name": name, "cause": "command"})),
				// The character cannot make it: a stand-in played (a smile and raised brows for a wave).
				Ok(Start::Instead(i)) => self.events.emit(
					json!({"event": "gesture", "name": name, "cause": "command", "played": self.motion.clip_name(i)}),
				),
				// It starts (and is reported, as "queued") when the one before it is done.
				Ok(Start::Queued) => self.events.emit(json!({"event": "gesture_waiting", "name": name})),
				Ok(Start::Skipped(why)) => {
					self.events.emit(json!({"event": "gesture_skipped", "name": name, "cause": "command", "why": why}))
				}
				Err(e) => self.events.emit(json!({"event": "error", "message": e})),
			},
			Command::RandomGestures(on) => self.motion.set_random(on),
			Command::Perform(perf) => self.speech.start(perf),
			Command::Syllable(k) => {
				for (name, strength) in self.speech.syllable(k) {
					match self.motion.suggest(&name, strength) {
						Ok(Start::Now) => self.events.emit(json!({"event": "gesture", "name": name, "cause": "speech"})),
						Ok(Start::Instead(i)) => self.events.emit(
							json!({"event": "gesture", "name": name, "cause": "speech", "played": self.motion.clip_name(i)}),
						),
						Ok(Start::Skipped(why)) => self
							.events
							.emit(json!({"event": "gesture_skipped", "name": name, "cause": "speech", "why": why})),
						_ => {}
					}
				}
			}
			Command::Expression(name) => {
				let index = match &name {
					None => None,
					Some(n) => match self.expressions.iter().position(|e| &e.name == n) {
						Some(i) => Some(i),
						None => {
							self.events.emit(json!({"event": "error", "message": format!("this model has no expression '{n}'")}));
							return;
						}
					},
				};
				for (i, (_, target)) in self.expression_weights.iter_mut().enumerate() {
					*target = if Some(i) == index { 1.0 } else { 0.0 };
				}
				self.motion.set_face(index.is_none());
			}
			Command::TryAnimation(path) => self.try_animation(&path),
			Command::Overlay { slot, image, anchor } => self.overlay(&slot, image.as_deref(), anchor),
			Command::Screen(screen) => {
				if let Some(overlays) = &mut self.overlays {
					if overlays.screen() != screen {
						overlays.set_screen(screen);
						self.events.emit(json!({"event": "screen", "screen": screen.name()}));
					}
				}
			}
			Command::ModelMotions(strength) => {
				if let Puppet::Live2d(p) = &self.puppet {
					if p.idle.is_some() && !self.opts.still {
						self.own_strength = strength;
					}
				}
			}
			Command::Live(values) => self.live.set(&values),
			Command::Quit => self.quit = true,
		}
	}

	/// Show a picture from the overlay folder in a slot, or fade the slot out.
	fn overlay(&mut self, slot: &str, image: Option<&str>, anchor: overlay::Anchor) {
		let (Some(overlays), Some((_, aux))) = (&mut self.overlays, &self.on_window) else {
			self.events.emit(json!({"event": "error", "message": "pictures cannot be shown over this character"}));
			return;
		};
		let Some(name) = image else {
			overlays.hide(slot);
			return;
		};
		let Some(folder) = &self.opts.overlays else {
			self.events.emit(json!({"event": "error", "message": "the stage has no overlay folder (--overlays)"}));
			return;
		};
		let shown = overlay::load(folder, name).and_then(|picture| overlays.show(aux, slot, anchor, picture));
		if let Err(e) = shown {
			self.events.emit(json!({"event": "error", "message": e}));
		}
	}

	/// What the character is doing, once a second, for the core (and the settings page).
	/// A Live2D-format model's own idle motion, added under the device's movement: all of it at rest,
	/// less while the character listens, thinks or talks, and little while it dozes; a gesture holding
	/// part of the body takes most of that part back while it plays.
	fn own_motion(&mut self, dt: f32, resting: bool, values: &mut Vec<(String, Vec2)>) {
		let Puppet::Live2d(puppet) = &mut self.puppet else { return };
		if puppet.idle.is_none() || (self.own_strength <= 0.0 && self.own_share.value <= 0.001) {
			return;
		}
		let state = match self.behaviour.state() {
			State::Idle => 1.0,
			State::Thinking => 0.8,
			State::Speaking => 0.7,
			State::Listening => 0.6,
		};
		// Changes of strength (the owner's, or the character's state) ease in rather than jump.
		let target = self.own_strength * if resting { 0.3 } else { state };
		let share = self.own_share.update(target, dt).clamp(0.0, 1.0);
		let changes = puppet.own_motion(dt);
		let hold = self.motion.procedural_hold();
		let params = &self.info.params;
		let channels = &self.own_channels;
		live2d_motion::add_changes(
			values,
			&changes,
			|id| params.iter().find(|p| p.name == id).map(|p| (p.min[0], p.max[0], p.defaults[0])),
			|id| share * channels.get(id).map_or(1.0, |&c| 1.0 - OWN_MOTION_GIVE_WAY * hold[c]),
		);
	}

	fn heartbeat(&mut self, now: Instant) {
		self.frames_since_beat += 1;
		let since = now.duration_since(self.last_beat).as_secs_f32();
		if since < 1.0 {
			return;
		}
		let per_frame = |total: f64| (total * 1000.0 / self.frames_since_beat.max(1) as f64 * 100.0).round() / 100.0;
		let (screen, showing) = match &self.overlays {
			Some(o) => (o.screen().name(), o.showing()),
			None => ("on", Vec::new()),
		};
		self.events.emit(json!({
			"event": "heartbeat",
			"fps": (self.frames_since_beat as f64 / since as f64 * 10.0).round() / 10.0,
			"state": self.behaviour.state().name(),
			"mood": self.motion.mood(),
			"playing": self.motion.playing(),
			"channels": self
				.motion
				.holders()
				.into_iter()
				.map(|(channel, name)| (channel.to_string(), json!(name)))
				.collect::<serde_json::Map<String, serde_json::Value>>(),
			"budget": {
				"own": self.motion.budget_used().0,
				"big": self.motion.budget_used().1,
				"mood": self.motion.budget_used().2,
				"idle": self.motion.budget_used().3,
			},
			"drives": {
				"hour": (self.director.hour() * 100.0).round() / 100.0,
				"energy": (self.director.energy * 100.0).round() / 100.0,
				"attention": (self.director.attention * 100.0).round() / 100.0,
				"boredom": (self.director.boredom * 100.0).round() / 100.0,
				"sleepiness": (self.director.sleepiness * 100.0).round() / 100.0,
				"dozing": self.director.dozing,
			},
			// Average milliseconds per frame on the processor: moving the character, and
			// handing its drawing to the graphics chip (which then draws in parallel).
			"update_ms": per_frame(self.update_time),
			"draw_ms": per_frame(self.draw_time),
			"screen": screen,
			"overlays": showing,
			"own_motion": match &self.puppet {
				Puppet::Live2d(p) if self.own_strength > 0.0 => p.idle.as_ref().and_then(|i| i.current()).map(str::to_string),
				_ => None,
			},
		}));
		self.frames_since_beat = 0;
		self.last_beat = now;
		self.update_time = 0.0;
		self.draw_time = 0.0;
	}

	/// Save frames that tests asked for, before the buffers are swapped.
	fn capture(&mut self) {
		let Some((_, aux)) = &self.on_window else { return };
		while self.next_capture < self.opts.capture_at.len() && self.t >= self.opts.capture_at[self.next_capture] {
			if let Some(dir) = &self.opts.capture_dir {
				let path = dir.join(format!("frame_{:03}.png", self.next_capture));
				match capture::save_frame(aux, self.width, self.height, &path) {
					Ok(()) => self.events.emit(json!({"event": "captured", "file": path, "t": self.t})),
					Err(e) => self.events.emit(json!({"event": "error", "message": e})),
				}
			}
			self.next_capture += 1;
		}
	}

	/// Take a step down in a VRM avatar's drawing (frames came too slowly), and tell the core.
	fn step_down(&mut self, (level, fps): (usize, f32)) {
		let (Puppet::Vrm(puppet), Some((Renderer::Vrm(r), _))) = (&mut self.puppet, &mut self.on_window) else { return };
		match level {
			1 => r.outlines = false,
			2 => puppet.spring_every = 2,
			3 => r.set_scale(0.75),
			_ => r.set_scale(0.5),
		}
		self.events.emit(json!({
			"event": "quality", "level": level, "change": QUALITY_STEPS[level - 1],
			"fps": (fps * 10.0).round() / 10.0, "scale": r.scale(),
		}));
	}

	/// Play an animation clip once, on a VRM avatar, as it is in the file (to try one before keeping it).
	fn try_animation(&mut self, path: &std::path::Path) {
		let Puppet::Vrm(puppet) = &mut self.puppet else {
			self.events.emit(json!({"event": "error", "message": "only 3D (VRM) avatars play animation clips"}));
			return;
		};
		match anim::Clip::load(path) {
			Ok(mut clip) => {
				clip.name = "_try".into();
				clip.looped = false;
				puppet.add_clip(clip);
				self.motion.set_external(&clip_gestures(puppet));
				match self.motion.play("_try", 1.0) {
					Ok(_) => self.events.emit(json!({"event": "gesture", "name": "_try", "cause": "command"})),
					Err(e) => self.events.emit(json!({"event": "error", "message": e})),
				}
			}
			Err(e) => self.events.emit(json!({"event": "error", "message": format!("that animation cannot be played: {e}")})),
		}
	}
}

impl App for StageApp {
	fn resume_window(&mut self, gl: glow::Context, aux: glow::Context) {
		let version = aux.version().clone();
		let [r, g, b] = self.opts.background;
		unsafe { aux.clear_color(r, g, b, 1.0) };
		let made = match &mut self.puppet {
			Puppet::Inox(model) => OpenglRenderer::new(gl, model).map(Renderer::Inox).map_err(|e| e.to_string()),
			Puppet::Live2d(puppet) => Live2dRenderer::new(gl, puppet).map(|mut r| {
				r.background = self.opts.background;
				Renderer::Live2d(Box::new(r))
			}),
			Puppet::Vrm(puppet) => VrmRenderer::new(gl, puppet).map(|mut r| {
				r.background = self.opts.background;
				r.shot = self.opts.shot;
				if self.opts.quality == Some(Quality::Lite) {
					r.outlines = false;
				}
				if let Some(scale) = self.opts.draw_scale {
					r.set_scale(scale);
				}
				Renderer::Vrm(Box::new(r))
			}),
		};
		match made {
			Ok(mut renderer) => {
				match &mut renderer {
					Renderer::Inox(r) => r.resize(self.opts.width, self.opts.height),
					Renderer::Live2d(r) => r.resize(self.opts.width, self.opts.height),
					Renderer::Vrm(r) => r.resize(self.opts.width, self.opts.height),
				}
				self.width = self.opts.width;
				self.height = self.opts.height;
				self.overlays = match overlay::Overlays::new(&aux) {
					Ok(o) => Some(o),
					Err(e) => {
						self.warnings.push(format!("captions and signs cannot be shown: {e}"));
						None
					}
				};
				self.on_window = Some((renderer, aux));
				self.fit_camera();
				let controls: Vec<_> = self
					.mapper
					.targets()
					.iter()
					.map(|(c, t)| {
						json!({"control": c.key(), "param": t.param, "axis": if t.axis == 0 { "x" } else { "y" },
							"min": t.lo, "max": t.hi, "rest": t.mid})
					})
					.collect();
				let param_info: Vec<_> = self
					.info
					.params
					.iter()
					.map(|p| json!({"name": p.name, "vec2": p.is_vec2, "min": p.min, "max": p.max, "default": p.defaults}))
					.collect();
				self.events.emit(json!({
					"event": "ready",
					"puppet": self.info.name,
					"format": self.puppet.format(),
					"parts": self.info.part_count,
					"params": self.info.params.len(),
					"param_names": self.info.params.iter().map(|p| p.name.clone()).collect::<Vec<_>>(),
					"param_info": param_info,
					"controls": controls,
					"emotions": self.motion.emotions(),
					"gestures": self.motion.gestures(),
					"actions": self.motion.actions(),
					// When each gesture lands, seconds in: the core starts it that long before its word.
					"strokes": self
						.motion
						.strokes()
						.into_iter()
						.map(|(name, at)| (name, json!(at)))
						.collect::<serde_json::Map<String, serde_json::Value>>(),
					"expressions": self.info.expressions,
					// The window's size, for pictures made to fit it (a "size" event follows any change).
					"width": self.width,
					"height": self.height,
					"opengl": format!("{}{}.{}", if version.is_embedded { "ES " } else { "" }, version.major, version.minor),
				}));
				if let Some(warning) = self.map_warning.take() {
					self.events.emit(json!({"event": "warning", "message": warning}));
				}
				for warning in std::mem::take(&mut self.warnings) {
					self.events.emit(json!({"event": "warning", "message": warning}));
				}
				if let Puppet::Live2d(puppet) = &self.puppet {
					let mut features = puppet.features().to_json();
					features["motions"] = json!({
						"groups": puppet.motion_groups.iter().map(|(g, n)| (g.clone(), json!(n))).collect::<serde_json::Map<String, serde_json::Value>>(),
						"idle": puppet.idle.as_ref().map(|i| i.names()).unwrap_or_default(),
						"strength": self.own_strength,
					});
					self.events.emit(json!({"event": "features", "live2d": features}));
					for warning in &puppet.warnings {
						self.events.emit(json!({"event": "warning", "message": warning}));
					}
				}
				if let (Puppet::Vrm(puppet), Some((Renderer::Vrm(r), _))) = (&self.puppet, &self.on_window) {
					let mut features = puppet.features();
					features["texture_bytes"] = json!(r.texture_bytes);
					features["texture_side"] = json!(r.texture_side);
					features["depth_bits"] = json!(r.depth_bits);
					features["quality"] = json!({
						"setting": match self.opts.quality { Some(Quality::Lite) => "lite", Some(Quality::Normal) => "normal", _ => "auto" },
						"outlines": r.outlines,
						"spring_every": puppet.spring_every,
					});
					features["animation"] = match puppet.animator() {
						Some(a) => json!({
							"style": "full",
							"loops": a.clips().iter().filter(|c| c.looped).map(|c| c.name.clone()).collect::<Vec<_>>(),
							"gestures": a.one_offs().map(|c| c.name.clone()).collect::<Vec<_>>(),
							"clip_bytes": a.bytes(),
						}),
						None => json!({"style": "light"}),
					};
					self.events.emit(json!({"event": "features", "vrm": features}));
					for warning in &puppet.warnings {
						self.events.emit(json!({"event": "warning", "message": warning}));
					}
				}
			}
			Err(e) => {
				self.events.emit(json!({"event": "fatal", "message": format!("renderer could not start: {e}")}));
				self.quit = true;
			}
		}
		self.last = Instant::now();
		self.last_beat = self.last;
	}

	fn resize(&mut self, width: u32, height: u32) {
		if (width, height) != (self.width, self.height) {
			self.events.emit(json!({"event": "size", "width": width, "height": height}));
		}
		self.width = width;
		self.height = height;
		match &mut self.on_window {
			Some((Renderer::Inox(r), _)) => r.resize(width, height),
			Some((Renderer::Live2d(r), _)) => r.resize(width, height),
			Some((Renderer::Vrm(r), _)) => r.resize(width, height),
			None => {}
		}
		self.fit_camera();
	}

	fn draw(&mut self) -> bool {
		if self.quit {
			return false;
		}
		let now = Instant::now();
		let dt = match self.opts.fixed_dt {
			Some(dt) => dt,
			None => now.duration_since(self.last).as_secs_f32().min(0.1),
		};
		self.last = now;
		self.t += dt;

		let mut pending: Vec<Command> = self.commands.try_iter().collect();
		if let Some(script) = &mut self.script {
			pending.extend(script.take_due(self.t));
		}
		for command in pending {
			self.handle(command);
		}
		if let (Some(overlays), Some((_, aux))) = (&mut self.overlays, &self.on_window) {
			overlays.update(aux, dt);
		}
		if self.overlays.as_ref().is_some_and(|o| o.dark()) {
			// The screen is dark: nothing is moved or drawn, and frames come a few times a second.
			if let Some((_, aux)) = &self.on_window {
				unsafe {
					aux.clear_color(0.0, 0.0, 0.0, 1.0);
					aux.clear(glow::COLOR_BUFFER_BIT);
					let [r, g, b] = self.opts.background;
					aux.clear_color(r, g, b, 1.0);
				}
			}
			self.capture();
			self.heartbeat(now);
			if self.opts.fixed_dt.is_none() {
				std::thread::sleep(std::time::Duration::from_millis(100));
			}
			return !self.quit && !self.opts.exit_after.is_some_and(|limit| self.t >= limit);
		}

		let direction = self.director.update(dt);
		self.react(&direction.reactions);
		for (name, strength) in &direction.responses {
			match self.motion.suggest(name, *strength) {
				Ok(Start::Now) | Ok(Start::Instead(_)) => {
					self.events.emit(json!({"event": "gesture", "name": name, "cause": "listening"}))
				}
				Ok(Start::Skipped(why)) => {
					self.events.emit(json!({"event": "gesture_skipped", "name": name, "cause": "listening", "why": why}))
				}
				_ => {}
			}
		}
		// Models that close their eyes by changing shape (not by fading drawn lids) can stop half way.
		let smooth_eyelids = matches!(self.puppet, Puppet::Live2d(_) | Puppet::Vrm(_));
		self.behaviour.set_smooth_eyelids(smooth_eyelids);
		self.motion.set_smooth_eyelids(smooth_eyelids);
		self.motion.set_idle(&direction.idle, direction.mood_share);
		self.motion.set_pace(direction.pace);
		self.motion.set_resting(direction.resting);
		let (energy, beats) = self.motion.style();
		self.behaviour.set_style(energy * direction.liveliness, beats);
		self.behaviour.set_drowsy(direction.drowsy);
		// An avatar's own idle loop for the mood shows its posture; the mood still shows on the face.
		if let Puppet::Vrm(p) = &self.puppet {
			if let Some(a) = p.animator() {
				let mood = format!("idle_{}", self.motion.mood());
				self.motion.set_mood_posture(!a.has_loop(&mood));
			}
		}
		let mut pose = self.behaviour.update(dt);
		// While a person moves it live, the character starts no gestures or idle actions of its own.
		let own_motion = !self.opts.still && !self.live.is_on();
		if let Some((g, cause)) = self.motion.apply(dt, &mut pose, self.behaviour.state(), own_motion) {
			self.events.emit(json!({"event": "gesture", "name": g, "cause": cause}));
		}
		if let Puppet::Vrm(p) = &mut self.puppet {
			if p.animator().is_some() {
				let hold = self.motion.procedural_hold();
				p.set_scene(Scene {
					state: self.behaviour.state(),
					mood: self.motion.mood().to_string(),
					resting: direction.resting,
					layers: self.motion.external_layers(),
					arm_hold: [hold[5], hold[6]],
				});
			}
		}
		self.speech.apply(dt, self.behaviour.state() == State::Speaking, &mut pose);
		self.live.apply(dt, &mut pose);
		let mut values = self.mapper.resolve(&pose);
		self.own_motion(dt, direction.resting, &mut values);
		self.overrides.apply(dt, &mut values);
		for (expression, (weight, target)) in self.expressions.iter().zip(self.expression_weights.iter_mut()) {
			let w = weight.update(*target, dt);
			let info = &self.info;
			let default = |id: &str| info.params.iter().find(|p| p.name == id).map(|p| p.defaults[0]).unwrap_or(0.0);
			live2d::apply_expression(&mut values, default, expression, w);
		}

		let Some((renderer, aux)) = &mut self.on_window else {
			return !self.quit;
		};
		let started = Instant::now();
		let posed: Instant;
		match (&mut self.puppet, renderer) {
			(Puppet::Inox(model), Renderer::Inox(renderer)) => {
				renderer.clear();
				let puppet = &mut model.puppet;
				puppet.begin_frame();
				if let Some(params) = puppet.param_ctx.as_mut() {
					for (name, value) in &values {
						let _ = params.set(name, *value);
					}
				}
				puppet.end_frame(dt);
				posed = Instant::now();
				renderer.on_begin_draw(puppet);
				renderer.draw(puppet);
				renderer.on_end_draw(puppet);
			}
			(Puppet::Live2d(puppet), Renderer::Live2d(renderer)) => {
				puppet.update(&values, dt);
				posed = Instant::now();
				if let Err(e) = renderer.draw(puppet) {
					self.events.emit(json!({"event": "fatal", "message": format!("drawing failed: {e}")}));
					return false;
				}
			}
			(Puppet::Vrm(puppet), Renderer::Vrm(renderer)) => {
				puppet.update(&values, dt);
				posed = Instant::now();
				if let Err(e) = renderer.draw(puppet) {
					self.events.emit(json!({"event": "fatal", "message": format!("drawing failed: {e}")}));
					return false;
				}
			}
			_ => unreachable!("the renderer is made for the puppet's format"),
		}
		if let Some(overlays) = &self.overlays {
			overlays.draw(aux, self.width, self.height);
		}
		self.update_time += posed.duration_since(started).as_secs_f64();
		self.draw_time += posed.elapsed().as_secs_f64();

		self.capture();

		// Too slow for this device: give something up, one step at a time, and say so.
		let real = now.duration_since(self.last_real).as_secs_f32();
		self.last_real = now;
		if let Some(step) = self.watchdog.as_mut().and_then(|w| w.frame(real, self.t)) {
			self.step_down(step);
		}
		self.heartbeat(now);

		if let Some(limit) = self.opts.exit_after {
			if self.t >= limit {
				return false;
			}
		}
		!self.quit
	}
}

fn run(mut opts: Options) -> Result<(), String> {
	let events = Events::default();
	let mut warnings: Vec<String> = read_settings(&mut opts).into_iter().collect();
	let bytes = std::fs::read(&opts.puppet).map_err(|e| format!("cannot read {}: {e}", opts.puppet.display()))?;
	let (mut puppet, info) = if vrm::looks_like_vrm(&bytes) {
		if bytes.len() as u64 > vrm::MAX_FILE_BYTES {
			return Err(format!("{} is too large ({} bytes)", opts.puppet.display(), bytes.len()));
		}
		let profile = opts.profile.unwrap_or_else(Profile::auto);
		let mut puppet = VrmPuppet::from_bytes(bytes, profile)?;
		if !opts.physics {
			puppet.drop_physics();
		}
		if opts.quality == Some(Quality::Lite) {
			puppet.lite();
		}
		// The full-body style: its clips are read only for an avatar set to it.
		if opts.vrm_animation == Some(VrmStyle::Full) {
			// "builtin" in the list (as the core writes it) is the stage's own clips.
			let folders: Vec<PathBuf> = if opts.clip_folders.is_empty() {
				builtin_clip_folder().into_iter().collect()
			} else {
				opts.clip_folders
					.iter()
					.filter_map(|f| if f.as_os_str() == "builtin" { builtin_clip_folder() } else { Some(f.clone()) })
					.collect()
			};
			let (clips, problems) = anim::load_folders(&folders);
			warnings.extend(problems);
			if clips.is_empty() {
				warnings.push("no animation clips were found for the full-body style; the avatar moves in the light style".into());
			} else {
				puppet.animate(clips);
			}
		}
		let info = puppet.info.clone();
		(Puppet::Vrm(Box::new(puppet)), info)
	} else if live2d::looks_like_model3(&bytes) {
		drop(bytes);
		let mut puppet = Live2dPuppet::load(&opts.puppet)?;
		if !opts.physics {
			puppet.drop_physics();
		}
		let info = puppet.info.clone();
		(Puppet::Live2d(Box::new(puppet)), info)
	} else {
		let info = puppet_info::read(&bytes)?;
		let unsupported = puppet_info::unsupported_node_types(&info);
		if !unsupported.is_empty() {
			return Err(format!(
				"this puppet uses features the renderer does not support yet: {}",
				unsupported.join(", ")
			));
		}
		let mut model = parse_inp(bytes.as_slice()).map_err(|e| format!("cannot load puppet: {e}"))?;
		model.puppet.init_transforms();
		model.puppet.init_rendering();
		model.puppet.init_params();
		model.puppet.init_physics();
		(Puppet::Inox(model), info)
	};
	// The owner's movement settings for this character. They must never stop the character
	// showing: without them, or if they do not fit it, the automatic choice is used.
	let mut map_warning = None;
	let mapper = match &opts.map {
		Some(path) if path.exists() => {
			let loaded = std::fs::read_to_string(path)
				.map_err(|e| format!("cannot read {}: {e}", path.display()))
				.and_then(|text| serde_json::from_str(&text).map_err(|e| format!("{}: {e}", path.display())))
				.and_then(|spec| Mapper::with_overrides(&info, &spec));
			match loaded {
				Ok(m) => m,
				Err(e) => {
					map_warning = Some(format!("the movement settings were not used: {e}"));
					Mapper::auto(&info)
				}
			}
		}
		_ => Mapper::auto(&info),
	};
	let script = match &opts.script {
		Some(path) => {
			let text = std::fs::read_to_string(path).map_err(|e| format!("cannot read {}: {e}", path.display()))?;
			Some(Script::parse(&text)?)
		}
		None => None,
	};
	if let Some(dir) = &opts.capture_dir {
		std::fs::create_dir_all(dir).map_err(|e| format!("cannot create {}: {e}", dir.display()))?;
	}

	let (tx, rx) = channel();
	if opts.stdin {
		control::spawn_stdin(tx.clone(), events.clone());
	}
	if let Some(path) = &opts.socket {
		control::spawn_socket(path, tx.clone(), events.clone())?;
	}

	let mut builder = WindowBuilder::new()
		.with_title("Character stage")
		.with_resizable(true)
		.with_inner_size(winit::dpi::PhysicalSize::new(opts.width, opts.height));
	if opts.fullscreen {
		builder = builder.with_fullscreen(Some(Fullscreen::Borderless(None)));
	}
	// 3D avatars need a depth buffer; the flat formats do not.
	let depth = matches!(puppet, Puppet::Vrm(_));
	let frame = AppFrame::init(builder, opts.gl, depth).map_err(|e| format!("cannot open a window: {e}"))?;
	let mut behaviour = Behaviour::new(opts.seed);
	behaviour.set_idle_motion(!opts.still);
	let mut motion = Motion::new(Library::builtin(), |key| mapper.drives(key), opts.seed);
	// Each gesture a little different every time (unless holding still, for comparisons).
	motion.set_variation(!opts.still);
	if let Puppet::Vrm(p) = &puppet {
		motion.set_external(&clip_gestures(p));
	}
	let expressions = match &puppet {
		Puppet::Live2d(p) => p.expressions.clone(),
		Puppet::Vrm(p) => p.expressions.clone(),
		Puppet::Inox(_) => Vec::new(),
	};
	let expression_weights = expressions.iter().map(|_| (smooth::Spring::new(0.0, 0.3), 0.0)).collect();
	// A Live2D-format model's own idle motions: the device's blinks, mouth and mouth shapes stay its
	// own, whatever parameters the owner chose for them.
	let mut own_strength = 0.0;
	let mut own_channels = std::collections::HashMap::new();
	if let Puppet::Live2d(p) = &mut puppet {
		if let Some(idle) = &mut p.idle {
			own_strength = if opts.still { 0.0 } else { opts.model_motions.unwrap_or(1.0) };
			let seed = match opts.fixed_dt {
				Some(_) => opts.seed,
				None => std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_nanos() as u64).unwrap_or(opts.seed),
			};
			idle.reseed(seed);
			idle.leave(mapper.targets().iter().filter(|(c, _)| own_motion_left_alone(*c)).map(|(_, t)| t.param.clone()));
			for param in &info.params {
				let by_control = mapper.targets().iter().find(|(_, t)| t.param == param.name).and_then(|(c, _)| control_channel(*c));
				if let Some(channel) = by_control.or_else(|| live2d_motion::channel_by_name(&param.name)) {
					own_channels.insert(param.name.clone(), channel);
				}
			}
		}
	}
	// A VRM avatar's drawing is watched in real time (not when time is stepped for tests), unless the
	// owner asked for it as it is.
	let watchdog = match (&puppet, opts.quality, opts.fixed_dt) {
		(Puppet::Vrm(_), Some(Quality::Normal), _) | (Puppet::Vrm(_), _, Some(_)) => None,
		(Puppet::Vrm(_), Some(Quality::Lite), None) => Some(Watchdog::new(LITE_LEVEL)),
		(Puppet::Vrm(_), _, None) => Some(Watchdog::new(0)),
		_ => None,
	};
	let app = StageApp {
		overrides: Overrides::new(&info.params),
		live: live::Live::default(),
		behaviour,
		motion,
		speech: speech::Speech::new(),
		director: director::Director::new(),
		expressions,
		expression_weights,
		opts,
		puppet,
		info,
		mapper,
		commands: rx,
		script,
		events,
		on_window: None,
		width: 0,
		height: 0,
		t: 0.0,
		last: Instant::now(),
		frames_since_beat: 0,
		last_beat: Instant::now(),
		update_time: 0.0,
		draw_time: 0.0,
		next_capture: 0,
		quit: false,
		map_warning,
		warnings,
		watchdog,
		last_real: Instant::now(),
		overlays: None,
		own_strength,
		own_share: smooth::Spring::new(own_strength, 0.6),
		own_channels,
	};
	frame.run(app).map_err(|e| format!("stage stopped: {e}"))
}

fn main() {
	let args: Vec<String> = std::env::args().skip(1).collect();
	let opts = match parse_options(&args) {
		Ok(o) => o,
		Err(e) => {
			if !e.is_empty() {
				eprintln!("stage: {e}\n");
			}
			eprint!("{USAGE}");
			std::process::exit(if e.is_empty() { 0 } else { 2 });
		}
	};
	if let Err(e) = run(opts) {
		println!("{}", json!({"event": "fatal", "message": e}));
		eprintln!("stage: {e}");
		std::process::exit(1);
	}
}

#[cfg(test)]
mod tests {
	use super::*;

	fn args(list: &[&str]) -> Vec<String> {
		list.iter().map(|s| s.to_string()).collect()
	}

	#[test]
	fn parses_typical_options() {
		let o = parse_options(&args(&[
			"aka.inp", "--size", "600x800", "--gl", "es", "--background", "ff8000", "--capture-dir", "out",
			"--capture-at", "2, 1,3", "--fixed-dt", "0.02", "--exit-after", "4", "--no-stdin",
		]))
		.unwrap();
		assert_eq!((o.width, o.height), (600, 800));
		assert_eq!(o.gl, GlPreference::Es);
		assert_eq!(o.background, [1.0, 128.0 / 255.0, 0.0]);
		assert_eq!(o.capture_at, vec![1.0, 2.0, 3.0]);
		assert_eq!(o.fixed_dt, Some(0.02));
		assert!(!o.stdin);
	}

	#[test]
	fn the_watchdog_steps_down_only_when_frames_stay_slow() {
		// Whole three-second windows of frames: 16 a second is slow, 32 fast (exact steps in binary).
		let mut w = Watchdog::new(0);
		let mut t = 0.0;
		let mut windows = |w: &mut Watchdog, fps: f32, count: usize| {
			let mut steps = Vec::new();
			for _ in 0..(fps * 3.0) as usize * count {
				t += 1.0 / fps;
				if let Some(s) = w.frame(1.0 / fps, t) {
					steps.push(s.0);
				}
			}
			steps
		};
		assert!(windows(&mut w, 16.0, 2).is_empty(), "nothing while warming up");
		assert!(windows(&mut w, 32.0, 4).is_empty(), "fast enough");
		assert!(windows(&mut w, 16.0, 1).is_empty(), "one slow spell is not enough");
		assert!(windows(&mut w, 32.0, 1).is_empty());
		assert_eq!(windows(&mut w, 16.0, 2), vec![1], "slow twice running: outlines go");
		assert_eq!(windows(&mut w, 16.0, 10), vec![2, 3, 4], "then one step at a time, and no further");
		let lite = Watchdog::new(LITE_LEVEL);
		assert_eq!(lite.level, 2, "lite starts with outlines and half-rate springs already given up");
	}

	#[test]
	fn rejects_bad_options() {
		for bad in [
			vec![],
			vec!["a.inp", "b.inp"],
			vec!["a.inp", "--size", "600"],
			vec!["a.inp", "--size", "0x10"],
			vec!["a.inp", "--gl", "vulkan"],
			vec!["a.inp", "--background", "red"],
			vec!["a.inp", "--fixed-dt", "5"],
			vec!["a.inp", "--capture-at", "1"],
			vec!["a.inp", "--bogus"],
			vec!["a.inp", "--seed"],
			vec!["a.inp", "--view", "1,2,3"],
			vec!["a.inp", "--view", "5,0,1,1"],
		] {
			assert!(parse_options(&args(&bad)).is_err(), "{bad:?}");
		}
	}
}
