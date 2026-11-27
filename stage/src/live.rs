//! Live movement from the core: a person tracked by a phone's camera or a face-tracking app (the settings
//! page's "Copy me"), as control values that take over the controls they name.
//!
//! ```text
//! {"live": {"head_yaw": 0.3, "head_pitch": -0.1, "smile": 0.6}}
//! {"live": null}
//! ```
//!
//! Each update says where the named controls should be now (the same controls and values as the motion
//! library's tracks); the stage follows smoothly between updates, which come 10 to 60 times a second.
//! Controls an update does not name keep the character's own movement, and its breathing goes on. The
//! person's movement fades in over a fifth of a second; when updates stop for half a second (a phone
//! that went away), or `null` comes, the character takes its controls back over a third of a second.
//! While it is live the character starts no gestures or idle actions by itself (main.rs).

use crate::behaviour::Pose;
use crate::motion::{self, KEYS};

/// Seconds the shown value takes to follow an update (most of the way).
const FOLLOW: f32 = 0.05;
const FADE_IN: f32 = 0.2;
const FADE_OUT: f32 = 0.35;
/// Seconds without an update before the character takes its controls back.
const PATIENCE: f32 = 0.5;

/// Controls that only go one way from rest: 0 to 1.
const ONE_SIDED: [&str; 4] = ["mouth_open", "blink", "eye_smile", "cheek"];

pub struct Live {
	target: [Option<f32>; KEYS.len()],
	shown: [f32; KEYS.len()],
	/// 0: the character's own movement; 1: the person's.
	weight: f32,
	since: f32,
	on: bool,
}

impl Default for Live {
	fn default() -> Self {
		Live { target: [None; KEYS.len()], shown: [0.0; KEYS.len()], weight: 0.0, since: 0.0, on: false }
	}
}

impl Live {
	/// An update: where the named controls should be now. An empty one ends live movement.
	pub fn set(&mut self, values: &[(usize, f32)]) {
		if values.is_empty() {
			self.stop();
			return;
		}
		for &(key, value) in values {
			let value = if ONE_SIDED.contains(&KEYS[key]) { value.clamp(0.0, 1.5) } else { value.clamp(-1.5, 1.5) };
			if self.target[key].is_none() || self.weight == 0.0 {
				self.shown[key] = value;
			}
			self.target[key] = Some(value);
		}
		self.since = 0.0;
		self.on = true;
	}

	/// Hand the controls back to the character.
	pub fn stop(&mut self) {
		self.on = false;
	}

	/// Whether the person's movement shows at all now.
	pub fn is_on(&self) -> bool {
		self.on || self.weight > 0.0
	}

	/// Put the person's movement over the character's.
	pub fn apply(&mut self, dt: f32, pose: &mut Pose) {
		if !self.is_on() {
			return;
		}
		self.since += dt;
		if self.on && self.since > PATIENCE {
			self.on = false;
		}
		self.weight = if self.on {
			(self.weight + dt / FADE_IN).min(1.0)
		} else {
			(self.weight - dt / FADE_OUT).max(0.0)
		};
		if self.weight == 0.0 {
			self.target = [None; KEYS.len()];
			return;
		}
		let follow = 1.0 - (-dt / FOLLOW).exp();
		let w = self.weight * self.weight * (3.0 - 2.0 * self.weight);
		for key in 0..KEYS.len() {
			let Some(target) = self.target[key] else { continue };
			self.shown[key] += (target - self.shown[key]) * follow;
			let slot = motion::slot(pose, key);
			*slot += (self.shown[key] - *slot) * w;
		}
	}
}

/// The control named, as an index into `motion::KEYS`.
pub fn key(name: &str) -> Option<usize> {
	KEYS.iter().position(|k| *k == name)
}

#[cfg(test)]
mod tests {
	use super::*;

	fn run(live: &mut Live, seconds: f32, pose: &Pose) -> Pose {
		let mut out = pose.clone();
		let mut t = 0.0;
		while t < seconds {
			out = pose.clone();
			live.apply(1.0 / 60.0, &mut out);
			t += 1.0 / 60.0;
		}
		out
	}

	#[test]
	fn the_named_controls_follow_the_person_and_the_rest_are_left_alone() {
		let mut live = Live::default();
		let rest = Pose { head_roll: 0.2, smile: 0.1, ..Pose::default() };
		live.set(&[(key("head_yaw").unwrap(), 0.6), (key("smile").unwrap(), 0.8)]);
		let mut pose = rest.clone();
		live.apply(1.0 / 60.0, &mut pose);
		assert!(pose.head_yaw > 0.0 && pose.head_yaw < 0.2, "it fades in, not in one jump: {}", pose.head_yaw);
		let pose = run(&mut live, 0.3, &rest);
		assert!((pose.head_yaw - 0.6).abs() < 0.01, "{}", pose.head_yaw);
		assert!((pose.smile - 0.8).abs() < 0.01);
		assert_eq!(pose.head_roll, 0.2, "a control it does not name keeps the character's own movement");
	}

	#[test]
	fn it_follows_updates_smoothly() {
		let mut live = Live::default();
		let rest = Pose::default();
		live.set(&[(key("head_pitch").unwrap(), 0.0)]);
		run(&mut live, 0.4, &rest);
		live.set(&[(key("head_pitch").unwrap(), 1.0)]);
		let mut pose = rest.clone();
		live.apply(1.0 / 60.0, &mut pose);
		assert!(pose.head_pitch > 0.1 && pose.head_pitch < 0.4, "between updates it moves, without a jump: {}", pose.head_pitch);
		let pose = run(&mut live, 0.25, &rest);
		assert!(pose.head_pitch > 0.95);
	}

	#[test]
	fn it_gives_the_controls_back_when_updates_stop_or_it_is_told() {
		let rest = Pose { head_yaw: -0.3, ..Pose::default() };
		let mut live = Live::default();
		live.set(&[(key("head_yaw").unwrap(), 0.7)]);
		assert!((run(&mut live, 0.45, &rest).head_yaw - 0.7).abs() < 0.02, "half a second without an update is patience");
		let pose = run(&mut live, 0.9, &rest);
		assert_eq!(pose.head_yaw, -0.3, "then the character has it back");
		assert!(!live.is_on());

		live.set(&[(key("head_yaw").unwrap(), 0.7)]);
		run(&mut live, 0.3, &rest);
		live.stop();
		let mut pose = rest.clone();
		live.apply(1.0 / 60.0, &mut pose);
		assert!(pose.head_yaw > 0.5, "it eases back, not in one jump");
		assert_eq!(run(&mut live, 0.5, &rest).head_yaw, -0.3);
	}

	#[test]
	fn values_stay_in_range() {
		let mut live = Live::default();
		live.set(&[(key("mouth_open").unwrap(), -0.5), (key("head_roll").unwrap(), 9.0)]);
		let pose = run(&mut live, 0.4, &Pose::default());
		assert_eq!(pose.mouth_open, 0.0);
		assert!((pose.head_roll - 1.5).abs() < 0.01);
		live.set(&[]);
		assert!(run(&mut live, 0.5, &Pose::default()) == Pose::default(), "an empty update ends it");
	}
}
