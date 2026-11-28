//! Window and OpenGL context set-up.
//!
//! Adapted from the Inox2D OpenGL example (BSD-2-Clause, see
//! `vendor/inox2d-opengl/LICENSE`). The main change is that an OpenGL ES 3.0
//! context is requested first, because that is what low-cost ARM boards offer.

use std::error::Error;
use std::ffi::CString;
use std::num::NonZeroU32;

use raw_window_handle::HasWindowHandle;
use winit::event::{Event, WindowEvent};
use winit::event_loop::EventLoop;
use winit::window::{Window, WindowAttributes};

use glutin::config::ConfigTemplateBuilder;
use glutin::context::{ContextApi, ContextAttributesBuilder, GlProfile, NotCurrentContext, Version};
use glutin::display::GetGlDisplay;
use glutin::prelude::*;
use glutin::surface::SwapInterval;

use glutin_winit::{self, DisplayBuilder, GlWindow};

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum GlPreference {
	/// OpenGL ES 3.0 first, then desktop OpenGL.
	Auto,
	Es,
	Desktop,
}

pub trait App {
	/// Called once the window exists. `gl` is for the renderer; `aux` is a
	/// second handle to the same context for the stage's own calls.
	fn resume_window(&mut self, gl: glow::Context, aux: glow::Context);
	fn resize(&mut self, width: u32, height: u32);
	/// Draw one frame. Return false to exit.
	fn draw(&mut self) -> bool;
}

pub struct AppFrame {
	window: Option<Window>,
	event_loop: EventLoop<()>,
	gl_config: glutin::config::Config,
	gl_display: glutin::display::Display,
	not_current_gl_context: Option<NotCurrentContext>,
	window_attributes: WindowAttributes,
}

impl AppFrame {
	pub fn init(window_attributes: WindowAttributes, preference: GlPreference, depth: bool) -> Result<Self, Box<dyn Error>> {
		let event_loop = EventLoop::new()?;
		let mut template = ConfigTemplateBuilder::new().with_transparency(false).with_stencil_size(8);
		if depth {
			template = template.with_depth_size(24);
		}

		// Only Windows requires the window to be present before creating the display.
		let maybe_window_attributes = cfg!(target_os = "windows").then_some(window_attributes.clone());
		let display_builder = DisplayBuilder::new().with_window_attributes(maybe_window_attributes);

		let (window, gl_config) = display_builder.build(&event_loop, template, |configs| {
			// No multisampling: prefer the cheapest config (3D avatars asked for depth above).
			configs
				.reduce(|best, config| if config.num_samples() < best.num_samples() { config } else { best })
				.unwrap()
		})?;

		let raw_window_handle = window.as_ref().and_then(|window| window.window_handle().ok()).map(|handle| handle.as_raw());
		let gl_display = gl_config.display();

		let es = ContextAttributesBuilder::new()
			.with_context_api(ContextApi::Gles(Some(Version::new(3, 0))))
			.build(raw_window_handle);
		// The core profile: a Mac has no other above OpenGL 2.1, and asking for it everywhere keeps the
		// desktop path honest (no habits that only a compatibility profile allows).
		let desktop = ContextAttributesBuilder::new()
			.with_context_api(ContextApi::OpenGl(Some(Version::new(3, 3))))
			.with_profile(GlProfile::Core)
			.build(raw_window_handle);
		let attempts = match preference {
			GlPreference::Auto => vec![es, desktop],
			GlPreference::Es => vec![es],
			GlPreference::Desktop => vec![desktop],
		};
		let mut context = None;
		let mut last_error = None;
		for attrs in &attempts {
			match unsafe { gl_display.create_context(&gl_config, attrs) } {
				Ok(c) => {
					context = Some(c);
					break;
				}
				Err(e) => last_error = Some(e),
			}
		}
		let Some(context) = context else {
			return Err(format!("could not create an OpenGL context: {:?}", last_error).into());
		};

		Ok(Self {
			window,
			event_loop,
			gl_config,
			gl_display,
			not_current_gl_context: Some(context),
			window_attributes,
		})
	}

	pub fn run<A: App + 'static>(mut self, mut app: A) -> Result<(), Box<dyn Error>> {
		let mut state = None;

		// winit 0.30 still offers this closure form (its new form is a trait); it keeps this loop as it was.
		#[allow(deprecated)]
		self.event_loop.run(move |event, window_target| match event {
			Event::Resumed => {
				let window = self.window.take().unwrap_or_else(|| {
					let window_attributes = self.window_attributes.clone();
					glutin_winit::finalize_window(window_target, window_attributes, &self.gl_config).unwrap()
				});

				let attrs = window.build_surface_attributes(Default::default()).expect("the window has no handle to draw on");
				let gl_surface = unsafe {
					self.gl_config
						.display()
						.create_window_surface(&self.gl_config, &attrs)
						.unwrap()
				};
				let gl_context = (self.not_current_gl_context)
					.take()
					.unwrap()
					.make_current(&gl_surface)
					.unwrap();

				let display = self.gl_display.clone();
				let load = move || unsafe {
					glow::Context::from_loader_function(|symbol| {
						display.get_proc_address(&CString::new(symbol).unwrap()) as *const _
					})
				};
				let size = window.inner_size();
				app.resume_window(load(), load());
				if size.width != 0 && size.height != 0 {
					app.resize(size.width, size.height);
				}

				if let Err(e) = gl_surface.set_swap_interval(&gl_context, SwapInterval::Wait(NonZeroU32::new(1).unwrap())) {
					eprintln!("could not enable vsync: {e:?}");
				}
				assert!(state.replace((gl_context, gl_surface, window)).is_none());
			}
			Event::WindowEvent { event, .. } => match event {
				WindowEvent::Resized(size) => {
					if size.width != 0 && size.height != 0 {
						if let Some((gl_context, gl_surface, _)) = &state {
							gl_surface.resize(
								gl_context,
								NonZeroU32::new(size.width).unwrap(),
								NonZeroU32::new(size.height).unwrap(),
							);
							app.resize(size.width, size.height);
						}
					}
				}
				WindowEvent::CloseRequested => window_target.exit(),
				_ => (),
			},
			Event::AboutToWait => {
				if let Some((gl_context, gl_surface, window)) = &state {
					if !app.draw() {
						window_target.exit();
						return;
					}
					window.request_redraw();
					gl_surface.swap_buffers(gl_context).unwrap();
				}
			}
			_ => (),
		})?;

		Ok(())
	}
}
