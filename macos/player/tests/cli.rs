use rachel_player::parse;

#[test]
fn parses_source_and_resume() {
    let cfg = parse([
        "rachel-player",
        "/a/00.mp3",
        "--start-seconds",
        "12.5",
        "--position-file",
        "/p/pos.json",
    ])
    .unwrap();
    assert_eq!(cfg.source.to_str().unwrap(), "/a/00.mp3");
    assert!((cfg.start_seconds - 12.5).abs() < 1e-9);
    assert_eq!(cfg.position_file.unwrap().to_str().unwrap(), "/p/pos.json");
    assert!(cfg.dynamics.level); // leveler on by default
}

#[test]
fn no_level_and_compress_flags() {
    let cfg = parse(["rachel-player", "/a/00.mp3", "--no-level", "--compress"]).unwrap();
    assert!(!cfg.dynamics.level);
    assert!(cfg.dynamics.compress);
}

#[test]
fn missing_source_errors() {
    assert!(parse(["rachel-player"]).is_err());
}
