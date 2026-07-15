use anima_corefs::publication::{atomic_publish, publish_immutable};

#[cfg(unix)]
use std::os::unix::fs::PermissionsExt;
#[cfg(unix)]
use std::process::Command;

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

#[cfg(unix)]
#[test]
fn publications_remain_private_with_a_permissive_umask() {
    let root = std::env::temp_dir().join(format!(
        "anima-corefs-private-publication-parent-{}",
        std::process::id()
    ));
    let _ = std::fs::remove_dir_all(&root);
    std::fs::create_dir_all(&root).unwrap();

    let status = Command::new(std::env::current_exe().unwrap())
        .arg("--ignored")
        .arg("--exact")
        .arg("helper_process_publishes_with_permissive_umask")
        .arg("--nocapture")
        .env("ANIMA_COREFS_PRIVATE_PUBLICATION_ROOT", &root)
        .status()
        .unwrap();

    assert!(status.success());
    std::fs::remove_dir_all(root).unwrap();
}

#[cfg(unix)]
#[test]
#[ignore]
fn helper_process_publishes_with_permissive_umask() {
    let Some(root) = std::env::var_os("ANIMA_COREFS_PRIVATE_PUBLICATION_ROOT") else {
        return;
    };
    unsafe {
        libc::umask(0o022);
    }
    let root = std::path::PathBuf::from(root);
    let atomic = root.join("atomic.acore");
    let immutable = root.join("immutable.acore");

    atomic_publish(&atomic, b"atomic").unwrap();
    publish_immutable(&immutable, b"immutable").unwrap();

    assert_eq!(
        std::fs::metadata(atomic).unwrap().permissions().mode() & 0o777,
        0o600
    );
    assert_eq!(
        std::fs::metadata(immutable).unwrap().permissions().mode() & 0o777,
        0o600
    );
}
