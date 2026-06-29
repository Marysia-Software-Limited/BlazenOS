// Build script: regenerate `include/jessica_ffi.h` from the
// `#[no_mangle] pub extern "C"` surface in `src/lib.rs`.
//
// cbindgen is incremental: when only the header changes it will skip
// rewriting the file, so this is cheap to run on every `cargo build`.

use std::env;
use std::path::PathBuf;

fn main() {
    println!("cargo:rerun-if-changed=src");
    println!("cargo:rerun-if-changed=cbindgen.toml");
    println!("cargo:rerun-if-changed=build.rs");

    let crate_dir = env::var("CARGO_MANIFEST_DIR").expect("CARGO_MANIFEST_DIR");
    let crate_path = PathBuf::from(&crate_dir);
    let out_path = crate_path.join("include").join("jessica_ffi.h");
    let cfg_path = crate_path.join("cbindgen.toml");

    // Don't fail the build during docs.rs / IDE indexing if cbindgen
    // can't materialise the header — the .rs sources still compile.
    let Ok(config) = cbindgen::Config::from_file(&cfg_path) else {
        println!("cargo:warning=cbindgen.toml not found; skipping header gen");
        return;
    };

    match cbindgen::Builder::new()
        .with_crate(&crate_dir)
        .with_config(config)
        .generate()
    {
        Ok(bindings) => {
            if let Some(parent) = out_path.parent() {
                std::fs::create_dir_all(parent).ok();
            }
            bindings.write_to_file(&out_path);
        }
        Err(err) => {
            println!("cargo:warning=cbindgen generation failed: {err}");
        }
    }
}
