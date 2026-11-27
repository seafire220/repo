//! Moods and gestures on the generic controls (docs/24-motion.md).
//!
//! An emotion is a **mood**: it changes how the character moves (how lively,
//! how strong its beats with speech), its resting posture and face, and
//! which gestures it makes by itself. A **gesture** is a short clip added on
//! top of whatever the character is doing, so it blends into speaking and
//! idling instead of replacing them. Both are written once, in control units,
//! in `stage/motions/library.json`, and reach each character through its
//! calibration; a gesture is offered only to characters that have every
//! control it needs. A need may name alternatives ("arm_left_b|arm_right_b":
//! either elbow will do), so a character with one moving arm still puts a
//! hand on its hip; tracks for controls it lacks are left out.
//!
//! **Channels.** The body is split into channels (head, torso, eyes, brows, expression, left arm, right
//! arm). A gesture holds the channels it moves strongly, so others can play at the same time on the
//! rest: a nod plays through a wave. A gesture that matters more (one asked for, over one the words
//! call for, over one the mood starts by itself) takes a held channel over; the other lets go of it
//! with a short fade and carries on with the rest. While a gesture holds a channel, the living motion
//! there (drifting, beats) mostly gives way to it.
//!
//! **Budget.** The gestures the character starts by itself are kept to a few a minute, big ones (a wave,
//! a bow) to two, and the mood picks gestures it has not made lately, so it never flails or repeats
//! itself. A gesture asked for always plays, if the character can make it.

use std::collections::{HashMap, VecDeque};

use serde_json::Value;

use crate::behaviour::{Pose, State};
use crate::smooth::Spring;

/// The controls moods and gestures may move, in `Pose` field names.
pub const KEYS: [&str; 21] = [
	"head_yaw", "head_pitch", "head_roll", "body_yaw", "body_pitch", "body_roll", "gaze_x", "gaze_y", "brows",
	"brow_form", "smile", "eye_smile", "cheek", "arm_left", "arm_right", "arm_left_b", "arm_right_b", "shoulders",
	"mouth_open", "blink", "head_turn",
];

/// The pose's value for a control (an index into KEYS).
pub fn slot<'a>(pose: &'a mut Pose, key: usize) -> &'a mut f32 {
	match KEYS[key] {
		"head_yaw" => &mut pose.head_yaw,
		"head_pitch" => &mut pose.head_pitch,
		"head_roll" => &mut pose.head_roll,
		"body_yaw" => &mut pose.body_yaw,
		"body_pitch" => &mut pose.body_pitch,
		"body_roll" => &mut pose.body_roll,
		"gaze_x" => &mut pose.gaze_x,
		"gaze_y" => &mut pose.gaze_y,
		"brows" => &mut pose.brows,
		"brow_form" => &mut pose.brow_form,
		"smile" => &mut pose.smile,
		"eye_smile" => &mut pose.eye_smile,
		"cheek" => &mut pose.cheek,
		"arm_left" => &mut pose.arm_left,
		"arm_right" => &mut pose.arm_right,
		"arm_left_b" => &mut pose.arm_left_b,
		"arm_right_b" => &mut pose.arm_right_b,
		"mouth_open" => &mut pose.mouth_open,
		"blink" => &mut pose.blink,
		"head_turn" => &mut pose.head_turn,
		_ => &mut pose.shoulders,
	}
}

/// Parts of the body a gesture can occupy.
pub const CHANNELS: [&str; 9] =
	["head", "torso", "eyes", "brows", "expression", "arm_left", "arm_right", "mouth", "eyelids"];

/// The channel a control belongs to.
fn channel_of(key: usize) -> usize {
	match KEYS[key] {
		"head_yaw" | "head_pitch" | "head_roll" | "head_turn" => 0,
		"body_yaw" | "body_pitch" | "body_roll" | "shoulders" => 1,
		"gaze_x" | "gaze_y" => 2,
		"brows" | "brow_form" => 3,
		"smile" | "eye_smile" | "cheek" => 4,
		"arm_left" | "arm_left_b" => 5,
		"mouth_open" => 7,
		"blink" => 8,
		_ => 6,
	}
}

/// A gesture holds a channel where it moves a control at least this far; smaller moves only colour a
/// channel, and give way to a gesture that holds it.
const HOLD_LEVEL: f32 = 0.25;
/// How much of the living motion (drifting, breathing, beats) gives way on a channel a gesture holds.
const GIVE_WAY: f32 = 0.7;

/// Who started a gesture, by precedence: a reaction (looking up when spoken to, waking with a start);
/// one asked for (by the language model or the settings page); one the words call for or a new mood
/// brings; one the mood starts by itself; an idle action (a stretch, a look around).
pub const REACTION: u8 = 4;
pub const ASKED: u8 = 3;
pub const SPEECH: u8 = 2;
const MOOD: u8 = 1;
const IDLE: u8 = 0;

/// The budget for the gestures the character starts by itself (not asked for), per minute: all of
/// them; big ones; and those the mood starts with nothing said to prompt them.
const MINUTE: f32 = 60.0;
const OWN_PER_MINUTE: usize = 10;
const BIG_PER_MINUTE: usize = 2;
const MOOD_PER_MINUTE: usize = 6;
/// Idle actions have their own budget: nobody is talking, so they never crowd the conversation.
const IDLE_PER_MINUTE: usize = 4;
/// A gesture the mood picks is less likely again until this many seconds have passed (big ones longer),
/// and the one it picked last is much less likely next.
const FRESH_SMALL: f32 = 20.0;
const FRESH_BIG: f32 = 60.0;
const AGAIN: f32 = 0.3;

/// What became of a gesture that was asked for or suggested.
#[derive(Clone, Copy, Debug, PartialEq)]
pub enum Start {
	Now,
	/// Waiting for an asked-for gesture using the same part of the body.
	Queued,
	/// Not played, and why.
	Skipped(&'static str),
	/// The character cannot make it; this clip (by index) played in its place.
	Instead(usize),
}

/// Controls that run from 0 to 1; the rest run from -1 to 1.
fn one_sided(key: usize) -> bool {
	matches!(KEYS[key], "eye_smile" | "cheek" | "mouth_open" | "blink")
}

/// What a clip is for.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Kind {
	/// Offered to the language model, started by the words or the mood.
	Gesture,
	/// Something the character does by itself when nobody is talking.
	Idle,
	/// A quick response to something that happens.
	Reaction,
}

/// A gesture an avatar plays from its own animation clip (a VRM avatar's full-body style): how long
/// the clip lasts, the channels it moves a little and a lot, when it moves most, and the channels whose
/// tracks it plays instead of the library's (its face tracks still play).
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct External {
	pub duration: f32,
	pub touches: u16,
	pub holds: u16,
	pub stroke: f32,
	pub replaces: u16,
}

/// A gesture playing from a clip this frame, for the avatar's animation player.
#[derive(Clone, Debug, PartialEq)]
pub struct ExternalLayer {
	pub name: String,
	/// Seconds into the clip.
	pub at: f32,
	pub mirrored: bool,
	/// How much it shows on each channel.
	pub weights: [f32; CHANNELS.len()],
}

fn key_index(name: &str) -> Result<usize, String> {
	KEYS.iter().position(|k| *k == name).ok_or(format!("unknown control '{name}'"))
}

/// One way of playing a clip: its tracks, each a list of (seconds, value) keys.
#[derive(Clone, Debug)]
struct Take {
	tracks: Vec<(usize, Vec<(f32, f32)>)>,
	duration: f32,
}

/// A control's mirror image: the other arm, or the same control turned the other way.
fn mirrored_key(key: usize) -> (usize, f32) {
	let name = KEYS[key];
	let other = match name {
		"arm_left" => "arm_right",
		"arm_right" => "arm_left",
		"arm_left_b" => "arm_right_b",
		"arm_right_b" => "arm_left_b",
		_ => name,
	};
	let sign = if matches!(name, "head_yaw" | "head_turn" | "head_roll" | "body_yaw" | "body_roll" | "gaze_x") { -1.0 } else { 1.0 };
	(key_index(other).unwrap_or(key), sign)
}

/// Channels, mirrored: the arms swap.
fn mirrored_mask(mask: u16) -> u16 {
	let (left, right) = (1u16 << 5, 1u16 << 6);
	let mut m = mask & !(left | right);
	if mask & left != 0 {
		m |= right;
	}
	if mask & right != 0 {
		m |= left;
	}
	m
}

#[derive(Clone, Debug)]
pub struct Clip {
	pub name: String,
	/// Each need is met by any one of its controls.
	pub needs: Vec<Vec<usize>>,
	/// Ways of playing it, one chosen at random each time (the first when variation is off).
	takes: Vec<Take>,
	/// The longest take, in seconds.
	pub duration: f32,
	/// It may play mirrored (the other arm, the other way), at random or when only that side moves.
	pub mirror: bool,
	/// Its poses are exact (a hand on the hip): it varies in speed, never in size.
	pub exact: bool,
	/// What to play instead on a character that cannot make it.
	pub instead: Option<String>,
	/// When it lands (seconds in): the speech planner starts it this long before its word.
	pub stroke: f32,
	/// Whether the library gave its channels and stroke, rather than their being worked out.
	holds_given: bool,
	stroke_given: bool,
	/// Channels it holds while it plays, and all it moves (one bit per channel).
	pub holds: u16,
	pub touches: u16,
	pub kind: Kind,
	/// A big movement (a wave, a bow): rarer, and at most a couple a minute unless asked for.
	pub big: bool,
	/// Played from the avatar's own clip, if it has one.
	pub ext: Option<External>,
	/// Only an avatar with a clip of its name can make it (a full turn, which a picture cannot show).
	pub clip_only: bool,
}

impl Clip {
	/// The clip's offsets `t` seconds into a take, mirrored or not, each channel by its own weight;
	/// keys are joined by smooth steps.
	fn sample(&self, take: usize, mirrored: bool, t: f32, out: &mut [f32; KEYS.len()], weights: &[f32; CHANNELS.len()]) {
		for (key, points) in &self.takes[take.min(self.takes.len() - 1)].tracks {
			let value = match points.iter().position(|(at, _)| *at > t) {
				None => points.last().map(|p| p.1).unwrap_or(0.0),
				Some(0) => points[0].1,
				Some(i) => {
					let (t0, v0) = points[i - 1];
					let (t1, v1) = points[i];
					let u = ((t - t0) / (t1 - t0).max(1e-4)).clamp(0.0, 1.0);
					v0 + (v1 - v0) * u * u * (3.0 - 2.0 * u)
				}
			};
			let (key, sign) = if mirrored { mirrored_key(*key) } else { (*key, 1.0) };
			// The clip moves this part of the body instead.
			if self.replaces_as(mirrored) & (1 << channel_of(key)) != 0 {
				continue;
			}
			out[key] += sign * value * weights[channel_of(key)];
		}
	}

	/// The clip as the library writes it (for the settings page's editor).
	pub fn to_json(&self) -> Value {
		let take = |t: &Take| {
			let mut tracks = serde_json::Map::new();
			for (key, points) in &t.tracks {
				let round = |x: f32| ((x as f64) * 1000.0).round() / 1000.0;
				let keys: Vec<[f64; 2]> = points.iter().map(|p| [round(p.0), round(p.1)]).collect();
				tracks.insert(KEYS[*key].to_string(), serde_json::json!(keys));
			}
			Value::Object(tracks)
		};
		let mut out = serde_json::Map::new();
		let kind = match self.kind {
			Kind::Gesture => "gesture",
			Kind::Idle => "idle",
			Kind::Reaction => "reaction",
		};
		out.insert("kind".into(), kind.into());
		let needs: Vec<String> =
			self.needs.iter().map(|any| any.iter().map(|k| KEYS[*k]).collect::<Vec<_>>().join("|")).collect();
		out.insert("needs".into(), serde_json::json!(needs));
		out.insert("tracks".into(), take(&self.takes[0]));
		if self.takes.len() > 1 {
			let more: Vec<Value> = self.takes[1..].iter().map(|t| serde_json::json!({"tracks": take(t)})).collect();
			out.insert("takes".into(), Value::Array(more));
		}
		for (flag, on) in [("big", self.big), ("mirror", self.mirror), ("exact", self.exact), ("clip_only", self.clip_only)] {
			if on {
				out.insert(flag.into(), true.into());
			}
		}
		if let Some(other) = &self.instead {
			out.insert("instead".into(), other.clone().into());
		}
		if self.holds_given {
			let names: Vec<&str> = (0..CHANNELS.len()).filter(|c| self.holds & (1 << c) != 0).map(|c| CHANNELS[c]).collect();
			out.insert("holds".into(), serde_json::json!(names));
		}
		if self.stroke_given {
			out.insert("stroke".into(), serde_json::json!(self.stroke));
		}
		Value::Object(out)
	}

	/// The channels it holds, mirrored or not (with its clip's, if it plays one).
	fn holds_as(&self, mirrored: bool) -> u16 {
		let h = self.holds | self.ext.map_or(0, |e| e.holds);
		if mirrored { mirrored_mask(h) } else { h }
	}

	fn touches_as(&self, mirrored: bool) -> u16 {
		let t = self.touches | self.ext.map_or(0, |e| e.touches);
		if mirrored { mirrored_mask(t) } else { t }
	}

	/// The channels its clip moves instead of its tracks.
	fn replaces_as(&self, mirrored: bool) -> u16 {
		let r = self.ext.map_or(0, |e| e.replaces);
		if mirrored { mirrored_mask(r) } else { r }
	}

	/// How long a take of it lasts (its clip's length, if longer).
	fn length(&self, take: usize) -> f32 {
		let own = self.takes[take.min(self.takes.len() - 1)].duration;
		self.ext.map_or(own, |e| own.max(e.duration))
	}

	/// When it lands: its clip's moment when it has no tracks of its own.
	fn stroke_now(&self) -> f32 {
		match self.ext {
			Some(e) if self.takes.iter().all(|t| t.tracks.is_empty()) => e.stroke,
			_ => self.stroke,
		}
	}
}

/// A clip's tracks, from `{"head_pitch": [[0, 0], [0.3, -0.4], [0.75, 0]], ...}`.
fn parse_take(name: &str, tracks: &Value) -> Result<Take, String> {
	let mut out = Vec::new();
	let mut duration: f32 = 0.0;
	for (key, points) in tracks.as_object().ok_or(format!("gesture {name}: no tracks"))? {
		let mut pts = Vec::new();
		for p in points.as_array().ok_or(format!("gesture {name}: track {key} must be a list"))? {
			let pair = p.as_array().filter(|a| a.len() == 2).ok_or(format!("gesture {name}: keys are [seconds, value]"))?;
			pts.push((number(&pair[0], "a key's time")?, number(&pair[1], "a key's value")?));
		}
		if pts.len() < 2 || pts.windows(2).any(|w| w[1].0 <= w[0].0) {
			return Err(format!("gesture {name}: track {key} needs two or more keys in time order"));
		}
		if pts[0].1 != 0.0 || pts.last().unwrap().1 != 0.0 {
			return Err(format!("gesture {name}: track {key} must start and end at 0"));
		}
		duration = duration.max(pts.last().unwrap().0);
		out.push((key_index(key)?, pts));
	}
	Ok(Take { tracks: out, duration })
}

#[derive(Clone, Debug)]
pub struct Mood {
	pub name: String,
	offsets: Vec<(usize, f32)>,
	energy: f32,
	beats: f32,
	random_every: (f32, f32),
	gestures: Vec<(String, f32)>,
	on_enter: Option<String>,
}

#[derive(Clone, Debug)]
pub struct Library {
	pub moods: Vec<Mood>,
	pub clips: Vec<Clip>,
}

fn number(v: &Value, what: &str) -> Result<f32, String> {
	v.as_f64().map(|x| x as f32).ok_or(format!("{what} must be a number"))
}

/// One clip of the library: a gesture (in "gestures"), or an idle action or reaction (in "actions", with
/// its "kind").
fn parse_clip(name: &String, g: &Value, section: Kind) -> Result<Clip, String> {
		let kind = match (section, g.get("kind").and_then(Value::as_str)) {
			(Kind::Gesture, _) => Kind::Gesture,
			(_, Some("idle")) => Kind::Idle,
			(_, Some("reaction")) => Kind::Reaction,
			_ => return Err(format!("action {name}: 'kind' must be idle or reaction")),
		};
		let needs = g
			.get("needs")
			.and_then(Value::as_array)
			.ok_or(format!("gesture {name}: no 'needs'"))?
			.iter()
			.map(|k| k.as_str().unwrap_or("").split('|').map(key_index).collect::<Result<Vec<_>, _>>())
			.collect::<Result<Vec<_>, _>>()?;
		let mut takes = vec![parse_take(name, g.get("tracks").unwrap_or(&Value::Null))?];
		for extra in g.get("takes").and_then(Value::as_array).map(|a| a.as_slice()).unwrap_or(&[]) {
			takes.push(parse_take(name, extra.get("tracks").unwrap_or(&Value::Null))?);
		}
		let duration = takes.iter().map(|t| t.duration).fold(0.0, f32::max);
		// The channels it holds: as listed, or those it moves far.
		let mut peak = [0.0f32; CHANNELS.len()];
		for (key, pts) in takes.iter().flat_map(|t| t.tracks.iter()) {
			let c = channel_of(*key);
			peak[c] = pts.iter().fold(peak[c], |m, p| m.max(p.1.abs()));
		}
		let touches = (0..CHANNELS.len()).filter(|c| peak[*c] > 0.0).fold(0u16, |m, c| m | 1 << c);
		let holds = match g.get("holds").and_then(Value::as_array) {
			Some(list) => {
				let mut m = 0u16;
				for c in list {
					let c = c.as_str().and_then(|n| CHANNELS.iter().position(|x| *x == n));
					m |= 1 << c.ok_or(format!("gesture {name}: 'holds' names channels: {}", CHANNELS.join(", ")))?;
				}
				m
			}
			None => {
				let strong = (0..CHANNELS.len()).filter(|c| peak[*c] >= HOLD_LEVEL).fold(0u16, |m, c| m | 1 << c);
				// At least the channel it moves most.
				let most = (0..CHANNELS.len()).max_by(|a, b| peak[*a].total_cmp(&peak[*b])).unwrap_or(0);
				if strong == 0 && peak[most] > 0.0 { 1 << most } else { strong }
			}
		};
		let big = g.get("big").and_then(Value::as_bool).unwrap_or(false);
		let holds_given = g.get("holds").and_then(Value::as_array).is_some();
		let stroke_given = g.get("stroke").is_some();
		// When it lands: as given, or when its strongest held move first nears its height.
		let stroke = match g.get("stroke") {
			Some(v) => number(v, "a stroke")?,
			None => {
				let held: Vec<&(usize, Vec<(f32, f32)>)> =
					takes[0].tracks.iter().filter(|(k, _)| holds & (1 << channel_of(*k)) != 0).collect();
				let top = held.iter().flat_map(|(_, p)| p.iter()).fold(0.0f32, |m, p| m.max(p.1.abs()));
				held.iter()
					.filter_map(|(_, p)| p.iter().find(|q| q.1.abs() >= 0.9 * top && top > 0.0).map(|q| q.0))
					.fold(f32::MAX, f32::min)
					.min(duration)
			}
		};
		Ok(Clip {
			name: name.clone(),
			needs,
			takes,
			duration,
			mirror: g.get("mirror").and_then(Value::as_bool).unwrap_or(false),
			exact: g.get("exact").and_then(Value::as_bool).unwrap_or(false),
			instead: g.get("instead").and_then(Value::as_str).map(str::to_string),
			stroke,
			holds_given,
			stroke_given,
			holds,
			touches,
			kind,
			big,
			ext: None,
			clip_only: g.get("clip_only").and_then(Value::as_bool).unwrap_or(false),
		})
}

impl Library {
	pub fn builtin() -> Self {
		Self::parse(include_str!("../motions/library.json")).expect("the built-in motion library is valid")
	}

	pub fn parse(text: &str) -> Result<Self, String> {
		let root: Value = serde_json::from_str(text).map_err(|e| format!("motion library: {e}"))?;
		let mut clips = Vec::new();
		let gestures = root.get("gestures").and_then(Value::as_object).ok_or("motion library has no gestures")?;
		let empty = serde_json::Map::new();
		let actions = root.get("actions").and_then(Value::as_object).unwrap_or(&empty);
		let all = gestures.iter().map(|g| (g, Kind::Gesture)).chain(actions.iter().filter(|(n, _)| !n.starts_with('_')).map(|g| (g, Kind::Idle)));
		for ((name, g), section) in all {
			clips.push(parse_clip(name, g, section)?);
		}
		let mut moods = Vec::new();
		for (name, m) in root.get("moods").and_then(Value::as_object).ok_or("motion library has no moods")? {
			let mut offsets = Vec::new();
			for (key, v) in m.get("offsets").and_then(Value::as_object).ok_or(format!("mood {name}: no offsets"))? {
				offsets.push((key_index(key)?, number(v, "an offset")?));
			}
			let every = m.get("random_every").and_then(Value::as_array).filter(|a| a.len() == 2);
			let every = every.ok_or(format!("mood {name}: random_every must be [min, max] seconds"))?;
			let mut gestures = Vec::new();
			for (g, w) in m.get("gestures").and_then(Value::as_object).ok_or(format!("mood {name}: no gestures"))? {
				if !clips.iter().any(|c| &c.name == g) {
					return Err(format!("mood {name}: no gesture called {g}"));
				}
				gestures.push((g.clone(), number(w, "a gesture's weight")?));
			}
			let on_enter = m.get("on_enter").and_then(Value::as_str).map(str::to_string);
			if let Some(g) = &on_enter {
				if !clips.iter().any(|c| &c.name == g) {
					return Err(format!("mood {name}: no gesture called {g}"));
				}
			}
			moods.push(Mood {
				name: name.clone(),
				offsets,
				energy: number(m.get("energy").unwrap_or(&Value::Null), "energy")?,
				beats: number(m.get("beats").unwrap_or(&Value::Null), "beats")?,
				random_every: (number(&every[0], "random_every")?, number(&every[1], "random_every")?),
				gestures,
				on_enter,
			});
		}
		if !moods.iter().any(|m| m.name == "neutral") {
			return Err("motion library needs a neutral mood".into());
		}
		for c in &clips {
			if let Some(other) = &c.instead {
				if !clips.iter().any(|x| &x.name == other) {
					return Err(format!("gesture {}: no gesture called {other} to play instead", c.name));
				}
			}
		}
		Ok(Self { moods, clips })
	}
}

struct Playing {
	clip: usize,
	start: f32,
	weight: f32,
	take: usize,
	mirrored: bool,
	/// Faster or slower than written, for variety.
	speed: f32,
	/// Who started it: ASKED, SPEECH or MOOD.
	priority: u8,
	/// When it was cut short by another gesture; it then fades out.
	cut: Option<f32>,
	/// When it let go of each channel to a gesture that took it over; it fades out there.
	released: [Option<f32>; CHANNELS.len()],
}

/// How much is left of something fading out since `since`.
fn fade(since: Option<f32>, t: f32) -> f32 {
	since.map(|c| (1.0 - (t - c) / CUT_FADE).max(0.0)).unwrap_or(1.0)
}

impl Playing {
	/// The channels it still holds.
	fn holding(&self, clip: &Clip) -> u16 {
		if self.cut.is_some() {
			return 0;
		}
		(0..CHANNELS.len()).filter(|c| self.released[*c].is_some()).fold(clip.holds_as(self.mirrored), |m, c| m & !(1 << c))
	}

	/// How far into its take it is.
	fn at(&self, t: f32) -> f32 {
		(t - self.start) * self.speed
	}
}

const MOOD_TIME: f32 = 0.5;
const CUT_FADE: f32 = 0.25;
/// Quiet time after a gesture before another starts by itself.
const RANDOM_REST: f32 = 0.8;

pub struct Motion {
	lib: Library,
	/// Whether the character has the controls for each clip as written, and mirrored.
	available: Vec<bool>,
	available_mirrored: Vec<bool>,
	/// Which controls the character has (for clips defined later).
	has: Vec<bool>,
	/// The library as built, to go back to when a changed clip is restored.
	builtin: Vec<Clip>,
	/// Clips added on the device and since taken away.
	removed: Vec<bool>,
	/// Vary each gesture's speed, size, take and side (off in tests that check exact values).
	vary: bool,
	mood: usize,
	intensity: f32,
	offsets: Vec<Spring>,
	energy: Spring,
	beats: Spring,
	playing: Vec<Playing>,
	last_end: f32,
	t: f32,
	random: bool,
	next_random: f32,
	rng: u64,
	/// False while the model's own expression shows the face: the mood then
	/// moves only posture and liveliness.
	face: bool,
	/// Gestures asked for while another asked-for one used the same channels; they follow it.
	queued: Vec<(usize, f32)>,
	/// How far each channel is held by gestures now (eased, so the living motion never jumps).
	busy: [f32; CHANNELS.len()],
	/// Gestures started in the last minute: (when, who started it, big), for the budget.
	recent: VecDeque<(f32, u8, bool)>,
	/// When each gesture last started, and the mood's last pick, for its choice of fresh ones.
	last_start: Vec<f32>,
	last_pick: Option<usize>,
	/// Idle actions to choose from while nobody is talking, with their weights (from the director).
	idle_pool: Vec<(usize, f32)>,
	/// How long the gaps between the character's own gestures are, against the mood's usual.
	pace: f32,
	/// Asleep: no gestures of its own.
	resting: bool,
	mood_share: f32,
	/// Whether the eyes can rest half closed; if not, a gesture's eyelids are open or shut, passing
	/// quickly between (see `Behaviour::set_smooth_eyelids`).
	smooth_eyelids: bool,
	/// Gestures and idle actions it never starts by itself (the owner's choice for this character).
	disabled: Vec<bool>,
	/// How expressive the character is (1 usual): the size of its own gestures.
	expressiveness: f32,
	/// Gestures the avatar plays from its own clips, by name.
	external: HashMap<String, External>,
	/// How far gestures without clips hold each channel (eased): an avatar's arms then follow the controls.
	proc_busy: [f32; CHANNELS.len()],
	/// Whether the mood moves the posture (head, body, arms); off while an avatar's own idle loop for
	/// the mood shows it.
	mood_posture: bool,
}

/// Asked-for gestures waiting their turn, at most.
const QUEUE: usize = 2;

/// Controls that belong to the face, which a model's own expression replaces.
const FACE: [&str; 5] = ["smile", "eye_smile", "cheek", "brows", "brow_form"];
/// Controls that make up the posture, which an avatar's own idle loop for a mood can show instead.
const POSTURE: [&str; 12] = [
	"head_yaw", "head_pitch", "head_roll", "body_yaw", "body_pitch", "body_roll", "arm_left", "arm_right", "arm_left_b",
	"arm_right_b", "shoulders", "head_turn",
];

impl Motion {
	/// `has` says whether the character has a parameter for a control.
	pub fn new(lib: Library, has: impl Fn(&str) -> bool, seed: u64) -> Self {
		let available =
			lib.clips.iter().map(|c| c.needs.iter().all(|any| any.iter().any(|k| has(KEYS[*k])))).collect();
		let available_mirrored = lib
			.clips
			.iter()
			.map(|c| c.mirror && c.needs.iter().all(|any| any.iter().any(|k| has(KEYS[mirrored_key(*k).0]))))
			.collect();
		let mood = lib.moods.iter().position(|m| m.name == "neutral").unwrap_or(0);
		let has: Vec<bool> = KEYS.iter().map(|k| has(k)).collect();
		let builtin = lib.clips.clone();
		let mut m = Self {
			lib,
			available,
			available_mirrored,
			has,
			builtin,
			removed: Vec::new(),
			vary: false,
			mood,
			intensity: 1.0,
			offsets: KEYS.iter().map(|_| Spring::new(0.0, MOOD_TIME)).collect(),
			energy: Spring::new(1.0, MOOD_TIME),
			beats: Spring::new(1.0, MOOD_TIME),
			playing: Vec::new(),
			last_end: 0.0,
			t: 0.0,
			random: true,
			next_random: 0.0,
			rng: seed.wrapping_mul(0x9E37_79B9_7F4A_7C15).max(1),
			face: true,
			queued: Vec::new(),
			busy: [0.0; CHANNELS.len()],
			recent: VecDeque::new(),
			last_start: Vec::new(),
			last_pick: None,
			idle_pool: Vec::new(),
			pace: 1.0,
			resting: false,
			mood_share: 0.5,
			smooth_eyelids: true,
			disabled: Vec::new(),
			expressiveness: 1.0,
			external: HashMap::new(),
			proc_busy: [0.0; CHANNELS.len()],
			mood_posture: true,
		};
		m.disabled = vec![false; m.lib.clips.len()];
		m.removed = vec![false; m.lib.clips.len()];
		m.last_start = vec![f32::NEG_INFINITY; m.lib.clips.len()];
		m.next_random = m.random_gap(1.0);
		m
	}

	/// Vary each gesture a little every time: speed, size, which take, which side.
	pub fn set_variation(&mut self, on: bool) {
		self.vary = on;
	}

	/// Whether the character can make a clip, as written or mirrored.
	fn playable(&self, i: usize) -> bool {
		let clip = &self.lib.clips[i];
		!self.removed[i] && (self.available[i] || self.available_mirrored[i]) && (!clip.clip_only || clip.ext.is_some())
	}

	/// Add a clip, or change one, from its library entry with its "name" and "kind" ("gesture", "idle" or
	/// "reaction"; a gesture by default). Returns its name.
	pub fn define(&mut self, entry: &Value) -> Result<String, String> {
		let name = entry.get("name").and_then(Value::as_str).unwrap_or("").to_string();
		if name.is_empty() || name.len() > 40 || !name.bytes().all(|b| b.is_ascii_lowercase() || b == b'_') {
			return Err("a gesture's name is lower-case letters and _".into());
		}
		let section = match entry.get("kind").and_then(Value::as_str) {
			None | Some("gesture") => Kind::Gesture,
			Some(_) => Kind::Idle,
		};
		let clip = parse_clip(&name, entry, section)?;
		if let Some(other) = &clip.instead {
			if !self.lib.clips.iter().any(|c| &c.name == other) {
				return Err(format!("gesture {name}: no gesture called {other} to play instead"));
			}
		}
		let mut clip = clip;
		clip.ext = self.external.get(&name).copied();
		let plain = clip.needs.iter().all(|any| any.iter().any(|k| self.has[*k]));
		let mirrored = clip.mirror && clip.needs.iter().all(|any| any.iter().any(|k| self.has[mirrored_key(*k).0]));
		match self.lib.clips.iter().position(|c| c.name == name) {
			Some(i) => {
				self.lib.clips[i] = clip;
				self.available[i] = plain;
				self.available_mirrored[i] = mirrored;
				self.removed[i] = false;
			}
			None => {
				self.lib.clips.push(clip);
				self.available.push(plain);
				self.available_mirrored.push(mirrored);
				self.removed.push(false);
				self.disabled.push(false);
				self.last_start.push(f32::NEG_INFINITY);
			}
		}
		Ok(name)
	}

	/// Undo a change: a built-in clip goes back to how it was built; one added on the device goes.
	pub fn undefine(&mut self, name: &str) -> Result<(), String> {
		let i = self.find(name)?;
		match self.builtin.iter().find(|c| c.name == name) {
			Some(original) => {
				self.lib.clips[i] = original.clone();
				self.lib.clips[i].ext = self.external.get(name).copied();
				let clip = &self.lib.clips[i];
				self.available[i] = clip.needs.iter().all(|any| any.iter().any(|k| self.has[*k]));
				self.available_mirrored[i] =
					clip.mirror && clip.needs.iter().all(|any| any.iter().any(|k| self.has[mirrored_key(*k).0]));
			}
			None => {
				self.removed[i] = true;
				for p in &mut self.playing {
					if p.clip == i {
						p.cut.get_or_insert(self.t);
					}
				}
				self.queued.retain(|q| q.0 != i);
			}
		}
		Ok(())
	}

	/// Every clip, as the library writes it, and whether it was changed or added on the device.
	pub fn describe(&self) -> Value {
		let mut out = serde_json::Map::new();
		for (i, clip) in self.lib.clips.iter().enumerate() {
			if self.removed[i] || clip.name.starts_with('_') {
				continue;
			}
			let mut entry = clip.to_json();
			let origin = match self.builtin.iter().find(|c| c.name == clip.name) {
				None => "added",
				Some(b) if b.to_json() != entry => "changed",
				Some(_) => "built in",
			};
			if let Value::Object(map) = &mut entry {
				map.insert("origin".into(), origin.into());
				// Whether this character can make it, itself or with a stand-in (and which).
				let resolved = self.resolve(i);
				map.insert("can".into(), resolved.is_some().into());
				// Played from the avatar's own animation clip (full-body 3D).
				if clip.ext.is_some() {
					map.insert("clip3d".into(), true.into());
				}
				if let Some(j) = resolved.filter(|j| *j != i) {
					map.insert("as".into(), self.lib.clips[j].name.clone().into());
				}
			}
			out.insert(clip.name.clone(), entry);
		}
		Value::Object(out)
	}

	/// The clip to play for clip `i`: itself, or what plays instead on a character that cannot make it.
	fn resolve(&self, i: usize) -> Option<usize> {
		let mut j = i;
		for _ in 0..4 {
			if self.playable(j) {
				return Some(j);
			}
			j = self.lib.clips.iter().position(|c| Some(&c.name) == self.lib.clips[j].instead.as_ref())?;
		}
		None
	}

	pub fn clip_name(&self, i: usize) -> &str {
		&self.lib.clips[i].name
	}

	/// When each gesture this character can make lands (seconds in), for timing it to a word.
	pub fn strokes(&self) -> Vec<(String, f32)> {
		self.lib
			.clips
			.iter()
			.enumerate()
			.filter(|(_, c)| c.kind == Kind::Gesture)
			.filter_map(|(i, c)| self.resolve(i).map(|j| (c.name.clone(), (self.lib.clips[j].stroke_now() * 100.0).round() / 100.0)))
			.collect()
	}

	pub fn emotions(&self) -> Vec<String> {
		let mut names: Vec<String> = self.lib.moods.iter().map(|m| m.name.clone()).collect();
		names.sort();
		names
	}

	/// Gestures this character can make (not its idle actions or reactions), itself or with a stand-in.
	pub fn gestures(&self) -> Vec<String> {
		let mut names: Vec<String> = (0..self.lib.clips.len())
			.filter(|i| self.lib.clips[*i].kind == Kind::Gesture && self.resolve(*i).is_some())
			.map(|i| self.lib.clips[i].name.clone())
			.collect();
		names.sort();
		names
	}

	pub fn mood(&self) -> &str {
		&self.lib.moods[self.mood].name
	}

	pub fn set_random(&mut self, on: bool) {
		self.random = on;
	}

	/// The idle actions to choose from while nobody is talking, by name and weight, and how much the
	/// mood's own gestures count among them.
	pub fn set_idle(&mut self, pool: &[(&str, f32)], mood_share: f32) {
		self.mood_share = mood_share.clamp(0.0, 1.0);
		self.idle_pool = pool
			.iter()
			.filter_map(|(name, w)| {
				let i = self.lib.clips.iter().position(|c| c.name == *name && c.kind == Kind::Idle)?;
				(self.playable(i) && *w > 0.0).then_some((i, *w))
			})
			.collect();
	}

	/// Gaps between the character's own gestures, against the mood's usual (2 is half as often).
	pub fn set_pace(&mut self, pace: f32) {
		self.pace = pace.clamp(0.25, 4.0);
	}

	/// Asleep: the character starts no gestures of its own.
	pub fn set_resting(&mut self, resting: bool) {
		self.resting = resting;
	}

	pub fn set_smooth_eyelids(&mut self, smooth: bool) {
		self.smooth_eyelids = smooth;
	}

	/// Gestures and idle actions the character never starts by itself (asked for, they still play).
	pub fn set_disabled(&mut self, names: &[String]) {
		self.disabled = self.lib.clips.iter().map(|c| names.contains(&c.name)).collect();
	}

	/// How expressive the character is, 1 usual: its own gestures are a little bigger or smaller.
	pub fn set_expressiveness(&mut self, x: f32) {
		self.expressiveness = x.clamp(0.4, 1.6);
	}

	/// Idle actions this character can make.
	pub fn actions(&self) -> Vec<String> {
		let mut names: Vec<String> = (0..self.lib.clips.len())
			.filter(|i| self.lib.clips[*i].kind == Kind::Idle && self.playable(*i))
			.map(|i| self.lib.clips[i].name.clone())
			.collect();
		names.sort();
		names
	}

	/// A reaction, now, over anything else. Returns whether it started.
	pub fn react(&mut self, name: &str) -> bool {
		match self.lib.clips.iter().position(|c| c.name == name).and_then(|i| self.resolve(i)) {
			Some(i) => self.begin(i, 1.0, REACTION) == Start::Now,
			None => false,
		}
	}

	/// Whether the mood shows on the face (off while a model's own expression does).
	pub fn set_face(&mut self, on: bool) {
		self.face = on;
	}

	/// The gestures this avatar plays from its own clips (replacing the list given before). Clips with
	/// no gesture of their name in the library become new gestures, asked for by name.
	pub fn set_external(&mut self, clips: &[(String, External)]) -> Vec<String> {
		self.external = clips.iter().cloned().collect();
		let mut added = Vec::new();
		for (name, e) in clips {
			if self.lib.clips.iter().enumerate().any(|(i, c)| &c.name == name && !self.removed[i]) {
				continue;
			}
			let entry = serde_json::json!({"name": name, "kind": "gesture", "needs": [], "tracks": {}, "big": e.duration > 2.5});
			if self.define(&entry).is_ok() {
				added.push(name.clone());
			}
		}
		for clip in &mut self.lib.clips {
			clip.ext = self.external.get(&clip.name).copied();
		}
		added
	}

	/// The gestures playing from clips now: each one's name, how far in, mirrored or not, and how much
	/// it shows on each channel. Its size counts a little (a smaller wave is still a wave).
	pub fn external_layers(&self) -> Vec<ExternalLayer> {
		self.playing
			.iter()
			.filter_map(|p| {
				let clip = &self.lib.clips[p.clip];
				clip.ext?;
				let whole = (0.75 + 0.25 * p.weight.min(1.0)) * fade(p.cut, self.t);
				let mut weights = [0.0f32; CHANNELS.len()];
				for (c, w) in weights.iter_mut().enumerate() {
					*w = whole * fade(p.released[c], self.t);
				}
				Some(ExternalLayer { name: clip.name.clone(), at: p.at(self.t), mirrored: p.mirrored, weights })
			})
			.collect()
	}

	/// How far gestures without clips hold each channel now.
	pub fn procedural_hold(&self) -> [f32; CHANNELS.len()] {
		self.proc_busy
	}

	/// Whether the mood moves the posture (head, body, arms) as well as the face.
	pub fn set_mood_posture(&mut self, on: bool) {
		self.mood_posture = on;
	}

	fn next_random_number(&mut self) -> f32 {
		self.rng ^= self.rng >> 12;
		self.rng ^= self.rng << 25;
		self.rng ^= self.rng >> 27;
		((self.rng.wrapping_mul(0x2545_F491_4F6C_DD1D) >> 40) as f32) / ((1u64 << 24) as f32)
	}

	fn random_gap(&mut self, scale: f32) -> f32 {
		let (lo, hi) = self.lib.moods[self.mood].random_every;
		self.t + scale * self.pace * (lo + self.next_random_number() * (hi - lo))
	}

	/// Change mood; plays its entry gesture if it has one (and the body is free for it). Returns the
	/// gesture started, if any.
	pub fn set_emotion(&mut self, name: &str, intensity: f32) -> Result<Option<String>, String> {
		let i = self.lib.moods.iter().position(|m| m.name == name).ok_or(format!("unknown emotion '{name}'"))?;
		let changed = i != self.mood;
		self.mood = i;
		self.intensity = intensity.clamp(0.0, 1.0);
		self.next_random = self.random_gap(1.0);
		if changed {
			if let Some(g) = self.lib.moods[i].on_enter.clone() {
				if let Some(c) = self.lib.clips.iter().position(|c| c.name == g).and_then(|c| self.resolve(c)) {
					if self.begin(c, self.intensity, SPEECH) == Start::Now {
						return Ok(Some(g));
					}
				}
			}
		}
		Ok(None)
	}

	fn find(&self, name: &str) -> Result<usize, String> {
		self.lib
			.clips
			.iter()
			.enumerate()
			.position(|(i, c)| c.name == name && !self.removed[i])
			.ok_or(format!("unknown gesture '{name}'"))
	}

	/// Play a gesture, as asked. It starts now, taking over the parts of the body it needs, unless
	/// another asked-for gesture is using them: then it waits for that one.
	pub fn play(&mut self, name: &str, intensity: f32) -> Result<Start, String> {
		let i = self.find(name)?;
		let j = self.resolve(i).ok_or(format!("this character cannot make the gesture '{name}'"))?;
		Ok(self.begin_for(i, j, intensity, ASKED))
	}

	/// Begin clip `j` in place of `i`, saying so if it is a stand-in.
	fn begin_for(&mut self, i: usize, j: usize, intensity: f32, priority: u8) -> Start {
		match self.begin(j, intensity, priority) {
			Start::Now if j != i => Start::Instead(j),
			other => other,
		}
	}

	/// A gesture the speech suggests (a nod on "yes"). It plays if the character can make it, nothing
	/// asked for is using the parts of the body it needs, and the budget allows.
	pub fn suggest(&mut self, name: &str, intensity: f32) -> Result<Start, String> {
		let i = self.find(name)?;
		match self.resolve(i) {
			Some(j) => Ok(self.begin_for(i, j, intensity, SPEECH)),
			None => Ok(Start::Skipped("this character cannot make it")),
		}
	}

	/// The budget, for gestures the character starts by itself.
	fn within_budget(&self, clip: usize, priority: u8) -> Result<(), &'static str> {
		if priority >= ASKED {
			return Ok(());
		}
		let (mut own, mut big, mut mood, mut idle) = (0, 0, 0, 0);
		for &(at, p, b) in &self.recent {
			if self.t - at >= MINUTE || p >= ASKED {
				continue;
			}
			if p == IDLE {
				idle += 1;
				continue;
			}
			own += 1;
			big += b as usize;
			mood += (p == MOOD) as usize;
		}
		if priority == IDLE {
			return if idle >= IDLE_PER_MINUTE { Err("enough idle actions this minute") } else { Ok(()) };
		}
		if own >= OWN_PER_MINUTE {
			return Err("enough gestures this minute");
		}
		if self.lib.clips[clip].big && big >= BIG_PER_MINUTE {
			return Err("enough big gestures this minute");
		}
		if priority == MOOD && mood >= MOOD_PER_MINUTE {
			return Err("enough of the mood's own gestures this minute");
		}
		Ok(())
	}

	fn begin(&mut self, i: usize, intensity: f32, priority: u8) -> Start {
		if priority < ASKED && priority != REACTION && self.disabled.get(i).copied().unwrap_or(false) {
			return Start::Skipped("switched off for this character");
		}
		if let Err(why) = self.within_budget(i, priority) {
			return Start::Skipped(why);
		}
		// Its own gestures are a little bigger for an expressive character, smaller for a reserved one.
		let intensity = if priority < ASKED { intensity * (0.7 + 0.3 * self.expressiveness) } else { intensity };
		// Which side, which take, how fast and how big: a little different each time.
		let clip = &self.lib.clips[i];
		let (can_plain, can_mirror) = (self.available[i], self.available_mirrored[i]);
		let (takes, exact, wants_vary) = (clip.takes.len(), clip.exact, self.vary);
		let mirrored = !can_plain || (wants_vary && can_mirror && self.next_random_number() < 0.5);
		let take = if wants_vary && takes > 1 { (self.next_random_number() * takes as f32) as usize % takes } else { 0 };
		let speed = if wants_vary { 0.88 + 0.24 * self.next_random_number() } else { 1.0 };
		let size = if wants_vary && !exact { 0.85 + 0.25 * self.next_random_number() } else { 1.0 };
		let holds = self.lib.clips[i].holds_as(mirrored);
		let conflicts: Vec<usize> = (0..self.playing.len())
			.filter(|j| self.playing[*j].holding(&self.lib.clips[self.playing[*j].clip]) & holds != 0)
			.collect();
		if conflicts.iter().any(|j| self.playing[*j].priority > priority) {
			return Start::Skipped("a gesture that matters more is using that part of the body");
		}
		// One asked-for gesture after another on the same part of the body: the next waits its turn.
		if priority == ASKED && conflicts.iter().any(|j| self.playing[*j].priority == ASKED) {
			if self.queued.len() < QUEUE {
				self.queued.push((i, intensity));
				return Start::Queued;
			}
			return Start::Skipped("two asked-for gestures are already waiting");
		}
		// The others let go of the channels it holds, and fade out there; one left holding nothing ends.
		let t = self.t;
		for p in &mut self.playing {
			let clip = &self.lib.clips[p.clip];
			for c in 0..CHANNELS.len() {
				if holds & (clip.holds_as(p.mirrored) | clip.touches_as(p.mirrored)) & (1 << c) != 0 {
					p.released[c].get_or_insert(t);
				}
			}
			if p.cut.is_none() && p.holding(clip) == 0 {
				p.cut = Some(t);
			}
		}
		// And it leaves alone the channels the others still hold.
		let others = self.playing.iter().fold(0u16, |m, p| m | p.holding(&self.lib.clips[p.clip]));
		let mut released = [None; CHANNELS.len()];
		for (c, r) in released.iter_mut().enumerate() {
			if others & self.lib.clips[i].touches_as(mirrored) & (1 << c) != 0 {
				*r = Some(t - CUT_FADE);
			}
		}
		self.playing.push(Playing {
			clip: i,
			start: t,
			weight: intensity.clamp(0.0, 1.5) * size,
			take,
			mirrored,
			speed,
			priority,
			cut: None,
			released,
		});
		self.recent.push_back((t, priority, self.lib.clips[i].big));
		self.last_start[i] = t;
		Start::Now
	}

	/// Which gesture holds each part of the body now (for the settings page's live view).
	pub fn holders(&self) -> Vec<(&'static str, String)> {
		let mut out = Vec::new();
		for (c, channel) in CHANNELS.iter().enumerate() {
			if let Some(p) = self.playing.iter().find(|p| p.holding(&self.lib.clips[p.clip]) & (1 << c) != 0) {
				out.push((*channel, self.lib.clips[p.clip].name.clone()));
			}
		}
		out
	}

	/// Gestures started in the last minute by the character itself, big ones among them, those the mood
	/// started, and idle actions: what the budget counts.
	pub fn budget_used(&self) -> (usize, usize, usize, usize) {
		let (mut own, mut big, mut mood, mut idle) = (0, 0, 0, 0);
		for &(at, p, b) in &self.recent {
			if self.t - at >= MINUTE || p >= ASKED {
				continue;
			}
			if p == IDLE {
				idle += 1;
			} else {
				own += 1;
				big += b as usize;
				mood += (p == MOOD) as usize;
			}
		}
		(own, big, mood, idle)
	}

	/// The gestures playing now (not cut short).
	pub fn playing(&self) -> Vec<String> {
		self.playing.iter().filter(|p| p.cut.is_none()).map(|p| self.lib.clips[p.clip].name.clone()).collect()
	}

	/// How lively idle motion should be, and how strong the beats with speech.
	pub fn style(&self) -> (f32, f32) {
		(self.energy.value, self.beats.value)
	}

	/// Advance and add the mood and any gestures to `pose`. Returns a gesture
	/// that started now without a command, and why: "queued" (asked for earlier,
	/// waiting its turn) or "mood" (started by the mood).
	pub fn apply(&mut self, dt: f32, pose: &mut Pose, state: State, idle_motion: bool) -> Option<(String, &'static str)> {
		self.t += dt;
		let k = self.intensity;
		let mut target = [0.0f32; KEYS.len()];
		let (energy, beats, mood_gestures) = {
			let mood = &self.lib.moods[self.mood];
			for (key, v) in &mood.offsets {
				let face = FACE.contains(&KEYS[*key]);
				let posture = POSTURE.contains(&KEYS[*key]);
				if !(face && !self.face) && !(posture && !self.mood_posture) {
					target[*key] = v * k;
				}
			}
			(1.0 + (mood.energy - 1.0) * k, 1.0 + (mood.beats - 1.0) * k, mood.gestures.clone())
		};
		self.energy.update(energy, dt);
		self.beats.update(beats, dt);

		let mut started = None;
		let was_playing = !self.playing.is_empty();
		self.playing.retain(|p| {
			let clip = &self.lib.clips[p.clip];
			let done = p.at(self.t) >= clip.length(p.take);
			let faded = p.cut.is_some_and(|c| self.t - c >= CUT_FADE);
			!(done || faded)
		});
		if was_playing && self.playing.is_empty() {
			self.last_end = self.t;
		}
		while self.recent.front().is_some_and(|r| self.t - r.0 >= MINUTE) {
			self.recent.pop_front();
		}
		// An asked-for gesture waiting its turn starts once no other asked-for one uses its channels.
		if let Some(&(i, intensity)) = self.queued.first() {
			let holds = self.lib.clips[i].holds_as(false);
			let blocked = self
				.playing
				.iter()
				.any(|p| p.priority == ASKED && p.holding(&self.lib.clips[p.clip]) & holds != 0);
			if !blocked {
				self.queued.remove(0);
				if self.begin(i, intensity, ASKED) == Start::Now {
					started = Some((self.lib.clips[i].name.clone(), "queued"));
				}
			}
		}
		if self.random
			&& idle_motion
			&& !self.resting
			&& self.playing.is_empty()
			&& self.t - self.last_end > RANDOM_REST
			&& self.t >= self.next_random
		{
			// Listening calls for small responses only; idling for fewer gestures, mixed with idle actions.
			let (pool, scale): (Vec<(String, f32)>, f32) = match state {
				State::Listening => (vec![("nod".into(), 2.0), ("tilt".into(), 1.0)], 1.0),
				State::Idle => {
					let share = self.mood_share;
					let mut pool: Vec<(String, f32)> = mood_gestures.iter().map(|(g, w)| (g.clone(), w * share)).collect();
					pool.extend(self.idle_pool.iter().map(|(i, w)| (self.lib.clips[*i].name.clone(), *w)));
					(pool, 1.5)
				}
				_ => (mood_gestures, 1.0),
			};
			// Gestures it can make, less likely if made lately.
			let pool: Vec<(usize, f32)> = pool
				.into_iter()
				.filter_map(|(g, w)| {
					let i = self.lib.clips.iter().position(|c| c.name == g).filter(|i| self.playable(*i) && !self.disabled[*i])?;
					let wait = if self.lib.clips[i].big { FRESH_BIG } else { FRESH_SMALL };
					let again = if self.last_pick == Some(i) { AGAIN } else { 1.0 };
					Some((i, w * again * ((self.t - self.last_start[i]) / wait).clamp(0.0, 1.0)))
				})
				.filter(|(_, w)| *w > 0.0)
				.collect();
			let total: f32 = pool.iter().map(|(_, w)| w).sum();
			if total > 0.0 {
				let mut pick = self.next_random_number() * total;
				for &(i, w) in &pool {
					if pick < w {
						let idle = self.lib.clips[i].kind == Kind::Idle;
						let strength = if state == State::Listening { 0.6 } else if idle { 1.0 } else { k.max(0.5) };
						if self.begin(i, strength, if idle { IDLE } else { MOOD }) == Start::Now {
							started = Some((self.lib.clips[i].name.clone(), if idle { "idle" } else { "mood" }));
							self.last_pick = Some(i);
						}
						break;
					}
					pick -= w;
				}
			}
			self.next_random = self.random_gap(scale);
		}

		// Each gesture adds its offsets, faded where it was cut short or let a channel go; the channels
		// gestures hold are busy, and the living motion there gives way, eased in and out.
		let mut add = [0.0f32; KEYS.len()];
		let mut held = [0.0f32; CHANNELS.len()];
		let mut held_by_tracks = [0.0f32; CHANNELS.len()];
		for p in &self.playing {
			let clip = &self.lib.clips[p.clip];
			let whole = p.weight * fade(p.cut, self.t);
			let mut weights = [0.0f32; CHANNELS.len()];
			let (holds, replaced) = (clip.holds_as(p.mirrored), clip.replaces_as(p.mirrored));
			let own = if p.mirrored { mirrored_mask(clip.holds) } else { clip.holds };
			for (c, w) in weights.iter_mut().enumerate() {
				*w = whole * fade(p.released[c], self.t);
				if holds & (1 << c) != 0 {
					held[c] = held[c].max(w.min(1.0));
				}
				if own & !replaced & (1 << c) != 0 {
					held_by_tracks[c] = held_by_tracks[c].max(w.min(1.0));
				}
			}
			clip.sample(p.take, p.mirrored, p.at(self.t), &mut add, &weights);
		}
		if !self.smooth_eyelids {
			let lids = KEYS.iter().position(|k| *k == "blink").unwrap_or(0);
			let u = ((add[lids] - 0.35) / 0.1).clamp(0.0, 1.0);
			add[lids] = u * u * (3.0 - 2.0 * u);
		}
		let step = dt / CUT_FADE;
		for (busy, target) in self.busy.iter_mut().zip(held) {
			*busy += (target - *busy).clamp(-step, step);
		}
		for (busy, target) in self.proc_busy.iter_mut().zip(held_by_tracks) {
			*busy += (target - *busy).clamp(-step, step);
		}
		for key in 0..KEYS.len() {
			*slot(pose, key) *= 1.0 - GIVE_WAY * self.busy[channel_of(key)];
		}
		for key in 0..KEYS.len() {
			let mood_offset = self.offsets[key].update(target[key], dt);
			let v = slot(pose, key);
			*v += mood_offset + add[key];
			*v = if one_sided(key) { v.clamp(0.0, 1.0) } else { v.clamp(-1.0, 1.0) };
		}
		started
	}
}

#[cfg(test)]
mod tests {
	use super::*;

	const DT: f32 = 1.0 / 60.0;

	fn all(_: &str) -> bool {
		true
	}

	fn run(m: &mut Motion, seconds: f32, state: State) -> (Vec<Pose>, Vec<String>) {
		let mut poses = Vec::new();
		let mut started = Vec::new();
		for _ in 0..(seconds / DT) as usize {
			let mut p = Pose::default();
			if let Some((g, _)) = m.apply(DT, &mut p, state, true) {
				started.push(g);
			}
			poses.push(p);
		}
		(poses, started)
	}

	fn ext(duration: f32, touches: u16, holds: u16) -> External {
		External { duration, touches, holds, stroke: 0.4, replaces: touches }
	}

	const ARMS: u16 = 1 << 5 | 1 << 6;

	#[test]
	fn a_gesture_with_a_clip_lasts_as_long_as_the_clip_and_keeps_only_its_face_tracks() {
		let mut m = Motion::new(Library::builtin(), all, 1);
		m.set_random(false);
		let added = m.set_external(&[("wave".into(), ext(3.0, 1 << 6 | 1, 1 << 6)), ("little_dance".into(), ext(4.0, ARMS | 3, ARMS | 2))]);
		assert_eq!(added, vec!["little_dance".to_string()], "a clip with no gesture of its name becomes one");
		assert!(m.gestures().contains(&"little_dance".to_string()));
		assert_eq!(m.describe()["wave"]["clip3d"], true);
		m.play("wave", 1.0).unwrap();
		let (poses, _) = run(&mut m, 2.5, State::Speaking);
		// The library's arm and head tracks give way to the clip; its smile still shows.
		assert!(poses.iter().all(|p| p.arm_right.abs() < 1e-4 && p.arm_left.abs() < 1e-4 && p.arm_right_b.abs() < 1e-4));
		assert!(poses.iter().all(|p| p.head_roll.abs() < 1e-4));
		assert!(poses.iter().any(|p| p.smile > 0.1));
		let layers = m.external_layers();
		assert_eq!(layers.len(), 1, "still playing past the library's 1.85 seconds");
		let wave = &layers[0];
		assert_eq!(wave.name, "wave");
		assert!((wave.at - 2.5).abs() < 0.05);
		assert!(wave.weights[6] > 0.7);
		assert_eq!(m.holders().iter().find(|(c, _)| *c == "arm_right").map(|(_, n)| n.as_str()), Some("wave"));
		run(&mut m, 0.6, State::Speaking);
		assert!(m.external_layers().is_empty(), "over when the clip is");
		// A new gesture from a clip: no tracks of its own, played for its clip's length.
		assert_eq!(m.play("little_dance", 1.0).unwrap(), Start::Now);
		run(&mut m, 3.9, State::Speaking);
		assert_eq!(m.external_layers().len(), 1);
		run(&mut m, 0.2, State::Speaking);
		assert!(m.external_layers().is_empty());
	}

	#[test]
	fn a_gesture_only_a_clip_can_show_is_offered_only_with_its_clip() {
		let mut m = Motion::new(Library::builtin(), all, 1);
		m.set_random(false);
		assert!(!m.gestures().contains(&"turn_around".to_string()), "a picture cannot turn round");
		assert!(m.play("turn_around", 1.0).is_err());
		m.set_external(&[("turn_around".into(), ext(4.6, 0b1100011, 0b1100011))]);
		assert!(m.gestures().contains(&"turn_around".to_string()));
		assert_eq!(m.play("turn_around", 1.0).unwrap(), Start::Now);
		let (poses, _) = run(&mut m, 1.0, State::Speaking);
		assert!(poses.iter().any(|p| p.smile > 0.1), "its face track plays");
	}

	#[test]
	fn arms_held_by_gestures_without_clips_are_told_apart() {
		let mut m = Motion::new(Library::builtin(), all, 1);
		m.set_random(false);
		m.set_external(&[("wave".into(), ext(2.0, 1 << 6, 1 << 6))]);
		m.play("hands_on_hips", 1.0).unwrap();
		run(&mut m, 1.0, State::Idle);
		let hold = m.procedural_hold();
		assert!(hold[5] > 0.9 && hold[6] > 0.9, "{hold:?}");
		let mut m = Motion::new(Library::builtin(), all, 1);
		m.set_random(false);
		m.set_external(&[("wave".into(), ext(2.0, 1 << 6, 1 << 6))]);
		m.play("wave", 1.0).unwrap();
		run(&mut m, 1.0, State::Idle);
		assert!(m.procedural_hold()[6] < 1e-3, "the clip moves the arm, not the controls");
	}

	#[test]
	fn the_mood_can_leave_the_posture_to_an_idle_loop() {
		let posture = |on: bool| {
			let mut m = Motion::new(Library::builtin(), all, 1);
			m.set_random(false);
			m.set_mood_posture(on);
			m.set_emotion("sad", 1.0).unwrap();
			let (poses, _) = run(&mut m, 2.0, State::Idle);
			let p = poses.last().unwrap().clone();
			(p.head_pitch, p.smile)
		};
		let (with_pitch, with_smile) = posture(true);
		let (without_pitch, without_smile) = posture(false);
		assert!(with_pitch.abs() > 0.05 && without_pitch.abs() < 1e-3, "{with_pitch} {without_pitch}");
		assert!((with_smile - without_smile).abs() < 1e-4 && with_smile < -0.05, "the face still shows the mood");
	}

	#[test]
	fn the_built_in_library_is_complete() {
		let lib = Library::builtin();
		for emotion in ["neutral", "happy", "sad", "angry", "surprised", "embarrassed", "thinking"] {
			assert!(lib.moods.iter().any(|m| m.name == emotion), "{emotion}");
		}
		for gesture in ["nod", "shake", "bow", "tilt", "wave", "hands_on_hips", "sway", "laugh", "shrug"] {
			assert!(lib.clips.iter().any(|c| c.name == gesture), "{gesture}");
		}
	}

	#[test]
	fn bad_libraries_are_refused() {
		for bad in [
			r#"{"moods": {}, "gestures": {}}"#,
			r#"{"moods": {"neutral": {"offsets": {"tail": 1}, "energy": 1, "beats": 1, "random_every": [1, 2], "gestures": {}}}, "gestures": {}}"#,
			r#"{"moods": {"neutral": {"offsets": {}, "energy": 1, "beats": 1, "random_every": [1, 2], "gestures": {"fly": 1}}}, "gestures": {}}"#,
			r#"{"moods": {"neutral": {"offsets": {}, "energy": 1, "beats": 1, "random_every": [1, 2], "gestures": {}}}, "gestures": {"g": {"needs": [], "tracks": {"head_yaw": [[0, 0], [1, 0.5]]}}}}"#,
		] {
			assert!(Library::parse(bad).is_err(), "{bad}");
		}
	}

	#[test]
	fn a_gesture_adds_to_the_pose_and_returns_to_it() {
		let mut m = Motion::new(Library::builtin(), all, 1);
		m.set_random(false);
		m.play("nod", 1.0).unwrap();
		let (poses, _) = run(&mut m, 1.2, State::Speaking);
		let lowest = poses.iter().map(|p| p.head_pitch).fold(f32::MAX, f32::min);
		assert!(lowest < -0.3, "the head dips: {lowest}");
		assert!(poses.last().unwrap().head_pitch.abs() < 1e-4, "and returns");
		assert!(poses.windows(2).all(|w| (w[1].head_pitch - w[0].head_pitch).abs() < 0.08), "smoothly");
	}

	#[test]
	fn a_mood_changes_the_face_and_the_liveliness_gradually() {
		let mut m = Motion::new(Library::builtin(), all, 1);
		m.set_random(false);
		assert_eq!(m.set_emotion("happy", 1.0).unwrap(), Some("bounce".to_string()));
		let (poses, _) = run(&mut m, 0.1, State::Idle);
		assert!(poses[0].smile < 0.1, "not at once");
		let (poses, _) = run(&mut m, 2.0, State::Idle);
		assert!((poses.last().unwrap().smile - 0.8).abs() < 0.02);
		let (energy, beats) = m.style();
		assert!(energy > 1.25 && beats > 1.25);
		m.set_emotion("sad", 0.5).unwrap();
		run(&mut m, 2.0, State::Idle);
		assert!(m.style().0 < 0.8, "half-strength sadness: {}", m.style().0);
		assert!(m.set_emotion("bored", 1.0).is_err());
	}

	/// The same run with one change made after `at` seconds.
	fn compare(first: &[(&str, u8)], at: f32, then: &[(&str, u8)], seconds: f32) -> (Vec<Pose>, Vec<Pose>) {
		let mut a = Motion::new(Library::builtin(), all, 1);
		let mut b = Motion::new(Library::builtin(), all, 1);
		for m in [&mut a, &mut b] {
			m.set_random(false);
			for (g, p) in first {
				m.begin(m.find(g).unwrap(), 1.0, *p);
			}
		}
		let (mut alone, _) = run(&mut a, at, State::Speaking);
		let (mut both, _) = run(&mut b, at, State::Speaking);
		for (g, p) in then {
			assert_eq!(b.begin(b.find(g).unwrap(), 1.0, *p), Start::Now, "{g}");
		}
		alone.extend(run(&mut a, seconds - at, State::Speaking).0);
		both.extend(run(&mut b, seconds - at, State::Speaking).0);
		(alone, both)
	}

	#[test]
	fn an_asked_for_gesture_is_not_disturbed_by_a_moods_entry_gesture() {
		let mut m = Motion::new(Library::builtin(), all, 1);
		m.set_random(false);
		m.play("wave", 1.0).unwrap();
		assert_eq!(m.set_emotion("happy", 1.0).unwrap(), Some("bounce".into()), "it bounces on the torso, free");
		let (poses, _) = run(&mut m, 1.0, State::Speaking);
		let mut w = Motion::new(Library::builtin(), all, 1);
		w.set_random(false);
		w.play("wave", 1.0).unwrap();
		let (alone, _) = run(&mut w, 1.0, State::Speaking);
		for (p, q) in poses.iter().zip(&alone) {
			assert!((p.arm_right - q.arm_right).abs() < 1e-5 && (p.arm_right_b - q.arm_right_b).abs() < 1e-5);
		}
		let mut m = Motion::new(Library::builtin(), all, 1);
		m.set_random(false);
		m.play("think", 1.0).unwrap();
		assert_eq!(m.set_emotion("surprised", 1.0).unwrap(), None, "a start in surprise would take the head from it");
	}

	#[test]
	fn a_nod_plays_through_a_wave() {
		let (alone, both) = compare(&[("wave", ASKED)], 0.3, &[("nod", SPEECH)], 1.6);
		for (p, q) in both.iter().zip(&alone) {
			assert!((p.arm_right - q.arm_right).abs() < 1e-5 && (p.arm_right_b - q.arm_right_b).abs() < 1e-5,
				"the arm waves on as if alone");
		}
		let dip = both.iter().map(|p| p.head_pitch).fold(f32::MAX, f32::min);
		assert!(dip < -0.3, "while the head nods: {dip}");
	}

	#[test]
	fn a_gesture_that_matters_more_takes_a_channel_and_the_other_keeps_the_rest() {
		// Thinking (a new mood's gesture) looks up and away; a tilt asked for takes the head from it.
		let (alone, both) = compare(&[("think", SPEECH)], 0.5, &[("tilt", ASKED)], 1.6);
		let at = (0.5 / DT) as usize;
		for (p, q) in both[at..].iter().zip(&alone[at..]) {
			assert!((p.gaze_x - q.gaze_x).abs() < 1e-5 && (p.gaze_y - q.gaze_y).abs() < 1e-5, "the eyes think on");
		}
		let later = at + (0.6 / DT) as usize;
		let mut tilt = Motion::new(Library::builtin(), all, 1);
		tilt.set_random(false);
		tilt.play("tilt", 1.0).unwrap();
		let (only, _) = run(&mut tilt, 1.1, State::Speaking);
		assert!((both[later].head_roll - only[(0.6 / DT) as usize].head_roll).abs() < 0.02, "the head tilts as asked");
	}

	#[test]
	fn a_suggested_gesture_never_takes_from_an_asked_one() {
		let mut m = Motion::new(Library::builtin(), all, 1);
		m.set_random(false);
		assert_eq!(m.play("wave", 1.0).unwrap(), Start::Now);
		assert!(matches!(m.suggest("hands_on_hips", 1.0).unwrap(), Start::Skipped(_)), "the right arm is waving");
		assert_eq!(m.suggest("nod", 1.0).unwrap(), Start::Now, "the head is free");
		assert_eq!(m.play("shake", 1.0).unwrap(), Start::Now, "asked: it takes the head from the nod");
		assert_eq!(m.playing(), vec!["wave".to_string(), "shake".to_string()]);
		assert!(matches!(m.suggest("brow_flash", 1.0).unwrap(), Start::Now), "the brows are free");
		assert_eq!(m.suggest("flap", 1.0).unwrap_err(), "unknown gesture 'flap'");
	}

	#[test]
	fn the_living_motion_gives_way_where_a_gesture_holds() {
		let mut m = Motion::new(Library::builtin(), all, 1);
		m.set_random(false);
		let mut out = Vec::new();
		m.play("wave", 1.0).unwrap();
		for _ in 0..(3.0 / DT) as usize {
			let mut p = Pose { arm_right: 0.05, arm_left: 0.05, ..Default::default() };
			m.apply(DT, &mut p, State::Speaking, true);
			out.push(p);
		}
		let mut w = Motion::new(Library::builtin(), all, 1);
		w.set_random(false);
		w.play("wave", 1.0).unwrap();
		let (bare, _) = run(&mut w, 3.0, State::Speaking);
		let mid = (1.0 / DT) as usize;
		let extra = out[mid].arm_right - bare[mid].arm_right;
		assert!((extra - 0.05 * (1.0 - GIVE_WAY)).abs() < 1e-3, "the living arm motion mostly gives way: {extra}");
		assert!((out[mid].arm_left - 0.05).abs() < 1e-5, "but not on the other arm");
		assert!((out.last().unwrap().arm_right - 0.05).abs() < 1e-5, "and comes back after");
		assert!(out.windows(2).all(|w| (w[1].arm_right - w[0].arm_right).abs() < 0.1), "smoothly");
	}

	#[test]
	fn no_channel_ever_jumps() {
		// The steepest any gesture moves by itself, frame to frame.
		let lib = Library::builtin();
		let mut steepest = 0.0f32;
		for clip in lib.clips.iter().filter(|c| !c.clip_only) {
			let mut m = Motion::new(Library::builtin(), all, 1);
			m.set_random(false);
			m.play(&clip.name, 1.0).unwrap();
			let (poses, _) = run(&mut m, clip.duration + 0.2, State::Speaking);
			steepest = steepest.max(largest_step(&poses));
		}
		// Gestures started on top of each other, taking parts of the body from each other, at odd moments.
		let mut m = Motion::new(Library::builtin(), all, 7);
		let mut poses = Vec::new();
		let script = [
			(0.0, "wave", ASKED), (0.2, "nod", SPEECH), (0.45, "shake", ASKED), (0.5, "brow_flash", SPEECH),
			(0.9, "hands_on_hips", ASKED), (1.1, "tilt", SPEECH), (1.3, "shrug", ASKED), (1.35, "laugh", SPEECH),
			(2.0, "think", SPEECH), (2.1, "look_away", ASKED), (2.2, "bow", SPEECH), (2.6, "lean_in", ASKED),
		];
		let mut next = 0;
		for i in 0..(6.0 / DT) as usize {
			let t = i as f32 * DT;
			while next < script.len() && script[next].0 <= t {
				let (_, g, p) = script[next];
				m.begin(m.find(g).unwrap(), 1.0, p);
				next += 1;
			}
			let mut p = Pose::default();
			m.apply(DT, &mut p, State::Speaking, true);
			poses.push(p);
		}
		let worst = largest_step(&poses);
		assert!(worst <= steepest * 1.25 + 1e-3, "{worst} against {steepest} for a gesture alone");
	}

	fn largest_step(poses: &[Pose]) -> f32 {
		let mut worst = 0.0f32;
		for w in poses.windows(2) {
			let (mut a, mut b) = (w[0], w[1]);
			for key in 0..KEYS.len() {
				worst = worst.max((*slot(&mut b, key) - *slot(&mut a, key)).abs());
			}
		}
		worst
	}

	#[test]
	fn gestures_per_minute_stay_in_a_band_over_ten_minutes() {
		// Ten minutes of conversation: 20 s of talking, then listening and quiet; a sentence every 2.5 s
		// with movements its words call for; now and then a new mood, and a gesture asked for.
		let mut m = Motion::new(Library::builtin(), all, 11);
		let moods = ["happy", "neutral", "thinking", "happy", "surprised", "sad", "angry", "embarrassed"];
		let words = ["nod", "tilt", "shake", "brow_flash", "wave", "laugh", "nod", "look_down", "shrug", "nod"];
		let mut rng = 5u64;
		let mut roll = || {
			rng ^= rng << 13;
			rng ^= rng >> 7;
			rng ^= rng << 17;
			(rng % 1000) as f32 / 1000.0
		};
		let mut per_minute = Vec::new();
		let frames = (600.0 / DT).round() as usize;
		for i in 0..frames {
			let t = i as f32 * DT;
			let cycle = t % 30.0;
			let state = if cycle < 20.0 { State::Speaking } else if cycle < 25.0 { State::Listening } else { State::Idle };
			if state == State::Speaking && (t % 2.5) < DT {
				if roll() < 0.15 {
					let _ = m.set_emotion(moods[(roll() * moods.len() as f32) as usize % moods.len()], 1.0);
				}
				if roll() < 0.6 {
					let _ = m.suggest(words[(roll() * words.len() as f32) as usize % words.len()], 0.8);
				}
				if roll() < 0.08 {
					let _ = m.play(["wave", "nod", "bow", "hands_on_hips"][(roll() * 4.0) as usize % 4], 1.0);
				}
			}
			let mut p = Pose::default();
			m.apply(DT, &mut p, state, true);
			// At the end of each minute, the gestures started in it: all of them, and big ones not asked for.
			if (i + 1) % (60.0 / DT).round() as usize == 0 {
				let all_count = m.recent.len();
				let own_big = m.recent.iter().filter(|r| r.1 < ASKED && r.2).count();
				per_minute.push((all_count, own_big));
			}
		}
		assert_eq!(per_minute.len(), 10);
		for (minute, (all_count, own_big)) in per_minute.iter().enumerate() {
			assert!((3..=14).contains(all_count), "minute {minute}: {all_count} gestures ({per_minute:?})");
			assert!(*own_big <= BIG_PER_MINUTE, "minute {minute}: {own_big} big ones not asked for");
		}
	}

	#[test]
	fn the_mood_varies_its_gestures() {
		let mut m = Motion::new(Library::builtin(), all, 3);
		let (_, started) = run(&mut m, 600.0, State::Speaking);
		assert!(started.len() > 20, "{}", started.len());
		let repeats = started.windows(2).filter(|w| w[0] == w[1]).count() as f32 / (started.len() - 1) as f32;
		// Without freshness the neutral mood repeats its favourite about 44% of the time.
		assert!(repeats < 0.3, "{repeats}: {started:?}");
	}

	#[test]
	fn gestures_asked_for_one_after_another_play_in_turn() {
		let mut m = Motion::new(Library::builtin(), all, 1);
		m.set_random(false);
		m.play("nod", 1.0).unwrap();
		m.play("tilt", 1.0).unwrap();
		let (poses, started) = run(&mut m, 3.0, State::Speaking);
		assert_eq!(started, vec!["tilt".to_string()], "the tilt starts when the nod is done");
		let nod_end = (0.75 / DT) as usize;
		assert!(poses[..nod_end - 2].iter().all(|p| p.head_roll.abs() < 1e-4), "no tilt during the nod");
		assert!(poses.iter().any(|p| p.head_roll > 0.4), "then the whole tilt");
	}

	#[test]
	fn gestures_need_the_controls_they_move_or_a_stand_in() {
		let only_head = |k: &str| k.starts_with("head") || k.starts_with("body");
		let mut m = Motion::new(Library::builtin(), only_head, 1);
		assert!(m.gestures().contains(&"nod".to_string()));
		// No arms: it waves with raised brows, a smile and a tilt instead.
		assert!(m.gestures().contains(&"wave".to_string()));
		match m.play("wave", 1.0).unwrap() {
			Start::Instead(i) => assert_eq!(m.clip_name(i), "greet"),
			other => panic!("{other:?}"),
		}
		// Nothing to move at all: nothing is offered, and nothing plays.
		let mut none = Motion::new(Library::builtin(), |_| false, 1);
		assert!(!none.gestures().contains(&"nod".to_string()));
		assert!(none.play("nod", 1.0).is_err());
		assert!(matches!(none.suggest("nod", 1.0).unwrap(), Start::Skipped(_)));
	}

	#[test]
	fn hands_on_hips_needs_an_elbow_and_bends_it() {
		let no_elbows = |k: &str| !k.ends_with("_b");
		let mut m = Motion::new(Library::builtin(), no_elbows, 1);
		match m.play("hands_on_hips", 1.0).unwrap() {
			Start::Instead(i) => assert_eq!(m.clip_name(i), "lean_back", "without elbows, a proud lean back instead"),
			other => panic!("{other:?}"),
		}
		let one_arm = |k: &str| !k.starts_with("arm_left");
		assert!(Motion::new(Library::builtin(), one_arm, 1).gestures().contains(&"hands_on_hips".to_string()),
			"one hand on the hip");
		assert!(Motion::new(Library::builtin(), no_elbows, 1).gestures().contains(&"wave".to_string()),
			"a wave still swings an arm without an elbow");
		let mut m = Motion::new(Library::builtin(), all, 1);
		m.set_random(false);
		m.play("hands_on_hips", 1.0).unwrap();
		let (poses, _) = run(&mut m, 1.5, State::Idle);
		let held = poses.last().unwrap();
		assert!((held.arm_left - 0.6).abs() < 0.05 && (held.arm_left_b + 0.9).abs() < 0.05, "{held:?}");
		assert!((held.arm_right - 0.6).abs() < 0.05 && (held.arm_right_b + 0.9).abs() < 0.05);
	}

	#[test]
	fn the_mood_brings_its_own_gestures_by_itself() {
		let mut m = Motion::new(Library::builtin(), all, 3);
		m.set_emotion("happy", 1.0).unwrap();
		let (_, started) = run(&mut m, 60.0, State::Speaking);
		assert!(started.len() >= 5, "{started:?}");
		let happy = ["bounce", "tilt", "nod", "sway", "laugh", "hands_on_hips", "wave"];
		assert!(started.iter().all(|g| happy.contains(&g.as_str())), "{started:?}");
		// While listening, only small responses.
		let mut l = Motion::new(Library::builtin(), all, 3);
		let (_, heard) = run(&mut l, 60.0, State::Listening);
		assert!(!heard.is_empty() && heard.iter().all(|g| g == "nod" || g == "tilt"), "{heard:?}");
		// Switched off, none.
		let mut off = Motion::new(Library::builtin(), all, 3);
		off.set_random(false);
		assert!(run(&mut off, 60.0, State::Speaking).1.is_empty());
	}

	#[test]
	fn a_new_gesture_cuts_the_old_one_short_without_a_jump() {
		let mut m = Motion::new(Library::builtin(), all, 1);
		m.set_random(false);
		m.play("tilt", 1.0).unwrap();
		let (before, _) = run(&mut m, 0.5, State::Speaking);
		m.play("nod", 1.0).unwrap();
		let (after, _) = run(&mut m, 0.5, State::Speaking);
		let jump = (after[0].head_roll - before.last().unwrap().head_roll).abs();
		assert!(jump < 0.08, "{jump}");
	}

	#[test]
	fn eyelids_that_swap_drawings_are_open_or_shut_in_gestures() {
		let mut m = Motion::new(Library::builtin(), all, 1);
		m.set_random(false);
		m.set_smooth_eyelids(false);
		m.play("yawn", 1.0).unwrap();
		let (poses, _) = run(&mut m, 3.0, State::Idle);
		let between = poses.iter().filter(|p| p.blink > 0.05 && p.blink < 0.95).count() as f32 / poses.len() as f32;
		assert!(between < 0.12, "{between}");
		assert!(poses.iter().any(|p| p.blink > 0.99), "shut while it yawns");
	}

	#[test]
	fn each_gesture_varies_a_little_but_exact_poses_keep_their_size() {
		let deepest = |seed: u64, name: &str, key: fn(&Pose) -> f32| {
			let mut m = Motion::new(Library::builtin(), all, seed);
			m.set_random(false);
			m.set_variation(true);
			m.play(name, 1.0).unwrap();
			let (poses, _) = run(&mut m, 2.0, State::Speaking);
			let end = poses.iter().rposition(|p| key(p).abs() > 1e-4).unwrap_or(0);
			(poses.iter().map(|p| key(p).abs()).fold(0.0f32, f32::max), end)
		};
		let tilts: Vec<(f32, usize)> = (1..9).map(|s| deepest(s, "tilt", |p| p.head_roll)).collect();
		let sizes: Vec<f32> = tilts.iter().map(|t| t.0).collect();
		let lengths: Vec<usize> = tilts.iter().map(|t| t.1).collect();
		assert!(sizes.iter().cloned().fold(0.0, f32::max) - sizes.iter().cloned().fold(1.0, f32::min) > 0.04, "{sizes:?}");
		assert!(lengths.iter().max().unwrap() - lengths.iter().min().unwrap() >= 6, "{lengths:?}");
		let hips: Vec<f32> = (1..9)
			.map(|seed| {
				let mut m = Motion::new(Library::builtin(), all, seed);
				m.set_random(false);
				m.set_variation(true);
				m.play("hands_on_hips", 1.0).unwrap();
				let (poses, _) = run(&mut m, 1.5, State::Idle);
				poses.last().unwrap().arm_right_b
			})
			.collect();
		assert!(hips.iter().all(|v| (v + 0.9).abs() < 0.03), "a hand stays on the hip: {hips:?}");
	}

	#[test]
	fn a_gesture_plays_mirrored_at_random_or_with_the_arm_that_moves() {
		let mut sides = (0, 0);
		for seed in 1..21 {
			let mut m = Motion::new(Library::builtin(), all, seed);
			m.set_random(false);
			m.set_variation(true);
			m.play("tilt", 1.0).unwrap();
			let (poses, _) = run(&mut m, 0.6, State::Speaking);
			if poses.last().unwrap().head_roll > 0.0 { sides.0 += 1 } else { sides.1 += 1 }
		}
		assert!(sides.0 >= 4 && sides.1 >= 4, "tilts both ways: {sides:?}");
		// Only the left arm moves: it waves with the left.
		let left_only = |k: &str| !k.starts_with("arm_right");
		let mut m = Motion::new(Library::builtin(), left_only, 1);
		m.set_random(false);
		assert_eq!(m.play("wave", 1.0).unwrap(), Start::Now);
		let (poses, _) = run(&mut m, 0.7, State::Speaking);
		assert!(poses.last().unwrap().arm_left > 0.4 && poses.last().unwrap().arm_right.abs() < 1e-5);
	}

	#[test]
	fn each_gesture_says_when_it_lands() {
		let m = Motion::new(Library::builtin(), all, 1);
		let strokes: std::collections::HashMap<String, f32> = m.strokes().into_iter().collect();
		assert!((strokes["nod"] - 0.18).abs() < 0.01, "the nod's lowest point: {}", strokes["nod"]);
		assert!(strokes["brow_flash"] < 0.15 && strokes["wave"] > 0.3 && strokes["bow"] > 0.5, "{strokes:?}");
		assert!(!strokes.contains_key("yawn"), "only gestures");
	}

	#[test]
	fn a_gesture_switched_off_is_never_started_by_the_character_itself() {
		let mut m = Motion::new(Library::builtin(), all, 3);
		m.set_disabled(&["nod".to_string(), "tilt".to_string()]);
		let (_, started) = run(&mut m, 120.0, State::Speaking);
		assert!(!started.is_empty() && started.iter().all(|g| g != "nod" && g != "tilt"), "{started:?}");
		assert!(matches!(m.suggest("nod", 1.0).unwrap(), Start::Skipped(_)));
		assert_eq!(m.play("nod", 1.0).unwrap(), Start::Now, "asked for, it still plays");
		assert!(m.actions().contains(&"yawn".to_string()) && !m.actions().contains(&"nod".to_string()));
	}

	#[test]
	fn clips_can_be_changed_added_and_put_back_on_the_device() {
		let head_only = |k: &str| k.starts_with("head");
		let mut m = Motion::new(Library::builtin(), head_only, 1);
		m.set_random(false);
		// A new gesture.
		let wiggle = serde_json::json!({"name": "wiggle", "needs": ["head_roll"],
			"tracks": {"head_roll": [[0, 0], [0.2, 0.4], [0.4, -0.4], [0.6, 0]]}});
		assert_eq!(m.define(&wiggle).unwrap(), "wiggle");
		assert!(m.gestures().contains(&"wiggle".to_string()));
		assert_eq!(m.play("wiggle", 1.0).unwrap(), Start::Now);
		let (poses, _) = run(&mut m, 0.7, State::Speaking);
		assert!(poses.iter().any(|p| p.head_roll > 0.3) && poses.iter().any(|p| p.head_roll < -0.3));
		// A changed nod, then back as built.
		let deep = serde_json::json!({"name": "nod", "needs": ["head_pitch"],
			"tracks": {"head_pitch": [[0, 0], [0.3, -0.9], [0.6, 0]]}});
		m.define(&deep).unwrap();
		assert_eq!(m.describe()["nod"]["origin"], "changed");
		m.play("nod", 1.0).unwrap();
		let deepest = run(&mut m, 0.7, State::Speaking).0.iter().map(|p| p.head_pitch).fold(0.0f32, f32::min);
		assert!(deepest < -0.8, "{deepest}");
		m.undefine("nod").unwrap();
		assert_eq!(m.describe()["nod"]["origin"], "built in");
		// The added one goes.
		m.undefine("wiggle").unwrap();
		assert!(!m.gestures().contains(&"wiggle".to_string()) && m.play("wiggle", 1.0).is_err());
		assert!(m.describe().get("wiggle").is_none());
		// Mistakes are refused, as in the library.
		for bad in [
			serde_json::json!({"name": "Bad Name", "needs": [], "tracks": {"head_roll": [[0, 0], [1, 0]]}}),
			serde_json::json!({"name": "jump", "needs": [], "tracks": {"head_roll": [[0, 0], [1, 0.5]]}}),
			serde_json::json!({"name": "jump", "needs": ["tail"], "tracks": {"head_roll": [[0, 0], [1, 0]]}}),
			serde_json::json!({"name": "jump", "needs": [], "instead": "fly", "tracks": {"head_roll": [[0, 0], [1, 0]]}}),
		] {
			assert!(m.define(&bad).is_err(), "{bad}");
		}
		// The library as written: tracks, needs and flags, and whether the character can make each clip.
		let all_clips = m.describe();
		assert_eq!(all_clips["wave"]["kind"], "gesture");
		assert_eq!(all_clips["wave"]["can"], true, "with its stand-in");
		assert_eq!(all_clips["wave"]["as"], "greet");
		assert_eq!(all_clips["yawn"]["kind"], "idle");
		assert_eq!(all_clips["glance"]["holds"], serde_json::json!(["head", "eyes"]));
		let reparsed = parse_clip(&"nod".to_string(), &all_clips["nod"], Kind::Gesture).unwrap();
		assert_eq!(reparsed.to_json()["tracks"], all_clips["nod"]["tracks"], "written as read");
	}
}
