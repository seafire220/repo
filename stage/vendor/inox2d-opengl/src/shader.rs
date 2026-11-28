use glow::HasContext;

#[derive(thiserror::Error, Debug)]
#[error("Could not compile shader: {0}")]
pub struct ShaderCompileError(String);

/// Compiles a shader program composed of a vertex and fragment shader.
pub(crate) fn compile(gl: &glow::Context, vertex: &str, fragment: &str) -> Result<glow::Program, ShaderCompileError> {
	unsafe {
		let program = gl.create_program().map_err(ShaderCompileError)?;

		// Use GLSL ES 3.00 on WebGL and on native OpenGL ES contexts (single-board computers).
		let es_sources;
		let (vertex, fragment) = if crate::is_gles(gl) {
			es_sources = (
				format!(
					"#version 300 es\nprecision highp float;\n{}",
					vertex.replace("#version 330", "")
				),
				format!(
					"#version 300 es\nprecision highp float;\n{}",
					fragment.replace("#version 330", "")
				),
			);
			(es_sources.0.as_str(), es_sources.1.as_str())
		} else {
			// Desktop OpenGL: the directive first, as on ES (a Mac's driver is strict about what comes before it).
			es_sources = (
				format!("#version 330 core\n{}", vertex.replace("#version 330", "")),
				format!("#version 330 core\n{}", fragment.replace("#version 330", "")),
			);
			(es_sources.0.as_str(), es_sources.1.as_str())
		};

		let shader = gl.create_shader(glow::VERTEX_SHADER).map_err(ShaderCompileError)?;
		gl.shader_source(shader, vertex);
		gl.compile_shader(shader);
		verify_shader(gl, shader)?;
		gl.attach_shader(program, shader);

		let shader = gl.create_shader(glow::FRAGMENT_SHADER).map_err(ShaderCompileError)?;
		gl.shader_source(shader, fragment);
		gl.compile_shader(shader);
		verify_shader(gl, shader)?;
		gl.attach_shader(program, shader);

		gl.link_program(program);
		verify_program(gl, program)?;

		Ok(program)
	}
}

unsafe fn verify_shader(gl: &glow::Context, shader: glow::Shader) -> Result<(), ShaderCompileError> {
	if gl.get_shader_compile_status(shader) {
		Ok(())
	} else {
		Err(ShaderCompileError(gl.get_shader_info_log(shader)))
	}
}

unsafe fn verify_program(gl: &glow::Context, program: glow::Program) -> Result<(), ShaderCompileError> {
	if gl.get_program_link_status(program) {
		Ok(())
	} else {
		Err(ShaderCompileError(gl.get_program_info_log(program)))
	}
}
