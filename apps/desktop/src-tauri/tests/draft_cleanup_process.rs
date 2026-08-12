use desktop_lib::draft_cleanup::{
    acquire_test_launch_gate, boot_identity_for_test, process_start_identity_for_test,
    verify_process_census_for_test,
};
use std::{
    fs,
    path::PathBuf,
    process::{Command, Stdio},
    thread,
    time::{Duration, Instant, SystemTime, UNIX_EPOCH},
};

const CHILD_ENV: &str = "ANIMA_DRAFT_CLEANUP_LOCK_CHILD";
const DIRECTORY_ENV: &str = "ANIMA_DRAFT_CLEANUP_LOCK_DIRECTORY";
const IDENTITY_CHILD_ENV: &str = "ANIMA_DRAFT_CLEANUP_IDENTITY_CHILD";
const CENSUS_CHILD_ENV: &str = "ANIMA_DRAFT_CLEANUP_CENSUS_CHILD";

#[test]
fn launch_gate_child() {
    if std::env::var_os(CHILD_ENV).is_none() {
        return;
    }
    let directory = PathBuf::from(std::env::var_os(DIRECTORY_ENV).expect("child directory"));
    let _lease = acquire_test_launch_gate(&directory).expect("child acquires launch gate");
    fs::write(directory.join("ready"), b"ready").expect("write child readiness");
    thread::sleep(Duration::from_secs(10));
}

#[test]
fn process_identity_child() {
    if std::env::var_os(IDENTITY_CHILD_ENV).is_some()
        || std::env::var_os(CENSUS_CHILD_ENV).is_some()
    {
        thread::sleep(Duration::from_secs(10));
    }
}

#[test]
fn native_census_blocks_a_live_competing_anima_executable() {
    if std::env::var_os(CENSUS_CHILD_ENV).is_some() {
        return;
    }
    let nonce = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap()
        .as_nanos();
    let directory = std::env::temp_dir().join(format!(
        "anima-draft-cleanup-census-{}-{nonce}",
        std::process::id()
    ));
    fs::create_dir_all(&directory).expect("create census fixture");
    let executable_name = if cfg!(windows) {
        "renamed-legacy-host.exe"
    } else {
        "renamed-legacy-host"
    };
    let executable = directory.join(executable_name);
    fs::copy(
        std::env::current_exe().expect("test executable"),
        &executable,
    )
    .expect("copy competing executable");
    let mut child = Command::new(&executable)
        .args(["--exact", "process_identity_child", "--nocapture"])
        .env(CENSUS_CHILD_ENV, "1")
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
        .expect("spawn competing anima executable");
    thread::sleep(Duration::from_millis(100));
    assert!(
        verify_process_census_for_test().is_err(),
        "census allowed a competing anima executable"
    );
    child.kill().expect("stop competing executable");
    child.wait().expect("reap competing executable");
    fs::remove_dir_all(&directory).expect("remove census fixture");
}

#[cfg(windows)]
#[test]
fn windows_launch_gate_replaces_hostile_explicit_acl_entries() {
    let nonce = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap()
        .as_nanos();
    let directory = std::env::temp_dir().join(format!(
        "anima-draft-cleanup-acl-{}-{nonce}",
        std::process::id()
    ));
    fs::create_dir_all(&directory).expect("create ACL fixture");
    let status = Command::new("icacls.exe")
        .arg(&directory)
        .args(["/grant", "*S-1-1-0:(OI)(CI)F"])
        .status()
        .expect("grant hostile Everyone ACE");
    assert!(status.success());
    let _lease = acquire_test_launch_gate(&directory).expect("replace and verify hostile ACL");
    let script = "$a=Get-Acl -LiteralPath $env:ANIMA_ACL_TEST; $bad=@($a.Access|Where-Object{$_.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value -eq 'S-1-1-0'}); if($bad.Count -ne 0){exit 2}";
    let status = Command::new("powershell.exe")
        .args(["-NoProfile", "-NonInteractive", "-Command", script])
        .env("ANIMA_ACL_TEST", &directory)
        .status()
        .expect("inspect protected ACL");
    assert!(
        status.success(),
        "Everyone ACE survived owner-only ACL replacement"
    );
    fs::remove_dir_all(&directory).expect("remove ACL fixture");
}

#[test]
fn exclusive_kernel_gate_blocks_a_second_process_and_releases_on_exit() {
    if std::env::var_os(CHILD_ENV).is_some() {
        return;
    }
    let nonce = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap()
        .as_nanos();
    let directory = std::env::temp_dir().join(format!(
        "anima-draft-cleanup-process-{}-{nonce}",
        std::process::id()
    ));
    let mut child = Command::new(std::env::current_exe().expect("test executable"))
        .args(["--exact", "launch_gate_child", "--nocapture"])
        .env(CHILD_ENV, "1")
        .env(DIRECTORY_ENV, &directory)
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
        .expect("spawn launch-gate contender");
    let deadline = Instant::now() + Duration::from_secs(5);
    while !directory.join("ready").is_file() && Instant::now() < deadline {
        thread::sleep(Duration::from_millis(20));
    }
    assert!(
        directory.join("ready").is_file(),
        "child did not acquire launch gate"
    );
    assert!(
        acquire_test_launch_gate(&directory).is_err(),
        "second process acquired the held launch gate"
    );
    child.kill().expect("stop child");
    child.wait().expect("reap child");
    let _replacement =
        acquire_test_launch_gate(&directory).expect("gate releases when owner exits");
    fs::remove_dir_all(&directory).expect("remove launch-gate fixture");
}

#[test]
fn native_boot_and_process_start_identities_are_precise_and_liveness_bound() {
    if std::env::var_os(CHILD_ENV).is_some() || std::env::var_os(IDENTITY_CHILD_ENV).is_some() {
        return;
    }
    match boot_identity_for_test() {
        Ok(identity) => assert!(!identity.is_empty()),
        Err(error) if std::env::var_os("ANIMA_REQUIRE_NATIVE_BOOT_IDENTITY_TEST").is_none() => {
            eprintln!("native boot identity is blocked by the local command sandbox: {error}");
        }
        Err(error) => panic!("native boot identity: {error}"),
    }
    let current = process_start_identity_for_test(std::process::id())
        .expect("inspect current process")
        .expect("current process is alive");
    assert!(current > 0);

    let mut child = Command::new(std::env::current_exe().expect("test executable"))
        .args(["--exact", "process_identity_child", "--nocapture"])
        .env(IDENTITY_CHILD_ENV, "1")
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
        .expect("spawn identity child");
    let child_identity = process_start_identity_for_test(child.id())
        .expect("inspect live child")
        .expect("child is alive");
    assert!(child_identity > 0);
    child.kill().expect("stop identity child");
    child.wait().expect("reap identity child");
    assert_eq!(
        process_start_identity_for_test(child.id()).expect("inspect reaped PID"),
        None
    );
}
