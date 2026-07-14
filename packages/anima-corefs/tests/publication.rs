use anima_corefs::publication::atomic_publish;

#[test]
fn atomic_publish_replaces_the_target_and_leaves_no_temporary_file() {
    let root = std::env::temp_dir().join(format!(
        "anima-corefs-publication-{}-{}",
        std::process::id(),
        std::thread::current().name().unwrap_or("test")
    ));
    let _ = std::fs::remove_dir_all(&root);
    std::fs::create_dir_all(&root).unwrap();
    let target = root.join("manifest.json");
    std::fs::write(&target, b"old").unwrap();

    atomic_publish(&target, b"new").unwrap();

    assert_eq!(std::fs::read(&target).unwrap(), b"new");
    assert_eq!(std::fs::read_dir(&root).unwrap().count(), 1);
    std::fs::remove_dir_all(root).unwrap();
}
