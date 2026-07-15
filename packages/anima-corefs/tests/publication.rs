use anima_corefs::publication::{atomic_publish, publish_immutable};

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

#[test]
fn immutable_publish_never_replaces_an_existing_revision() {
    let root = std::env::temp_dir().join(format!(
        "anima-corefs-immutable-publication-{}-{}",
        std::process::id(),
        std::thread::current().name().unwrap_or("test")
    ));
    let _ = std::fs::remove_dir_all(&root);
    std::fs::create_dir_all(&root).unwrap();
    let target = root.join("object.acore");

    publish_immutable(&target, b"first").unwrap();
    let error = publish_immutable(&target, b"second").unwrap_err();

    assert_eq!(error.kind(), std::io::ErrorKind::AlreadyExists);
    assert_eq!(std::fs::read(&target).unwrap(), b"first");
    assert_eq!(std::fs::read_dir(&root).unwrap().count(), 1);
    std::fs::remove_dir_all(root).unwrap();
}
