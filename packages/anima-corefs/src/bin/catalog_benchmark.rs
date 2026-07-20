use std::env;
use std::fs;
use std::path::PathBuf;

#[cfg(windows)]
use std::fs::{File, OpenOptions};
#[cfg(windows)]
use std::io;
#[cfg(windows)]
use std::os::windows::fs::OpenOptionsExt;
#[cfg(windows)]
use std::os::windows::io::AsRawHandle;

#[cfg(windows)]
use windows_sys::Win32::Storage::FileSystem::{
    GetFileInformationByHandle, BY_HANDLE_FILE_INFORMATION, FILE_ATTRIBUTE_DIRECTORY,
    FILE_ATTRIBUTE_REPARSE_POINT, FILE_FLAG_BACKUP_SEMANTICS, FILE_FLAG_OPEN_REPARSE_POINT,
    FILE_SHARE_READ, FILE_SHARE_WRITE,
};

use anima_corefs::benchmark::{
    build_fixture_matrix, run_fixture_benchmark, BenchmarkRunConfig, FixtureBenchmarkReport,
    REFERENCE_MEASURED_COMMITS, REFERENCE_WARMUP_COMMITS,
};
use serde::Serialize;

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct RunnerReport {
    schema_version: u16,
    warmup_commits: usize,
    measured_commits: usize,
    fixtures: Vec<FixtureBenchmarkReport>,
}

#[cfg(windows)]
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
struct DirectoryIdentity {
    volume_serial: u32,
    file_index: u64,
}

#[cfg(windows)]
struct PinnedDirectory {
    path: PathBuf,
    identity: DirectoryIdentity,
    _handle: File,
}

#[cfg(windows)]
impl PinnedDirectory {
    fn open(path: &std::path::Path) -> io::Result<Self> {
        let (handle, identity) = open_local_directory(path)?;
        Ok(Self {
            path: path.to_owned(),
            identity,
            _handle: handle,
        })
    }

    fn create_child(parent: &Self, name: &str) -> io::Result<Self> {
        Self::create_child_with(parent, name, |path| fs::create_dir(path))
    }

    fn create_child_with<F>(parent: &Self, name: &str, create: F) -> io::Result<Self>
    where
        F: FnOnce(&std::path::Path) -> io::Result<()>,
    {
        let name_path = std::path::Path::new(name);
        if name_path.file_name() != Some(name_path.as_os_str())
            || name_path.components().count() != 1
        {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "benchmark fixture name must be one path component",
            ));
        }
        parent.verify()?;
        let path = parent.path.join(name);
        // Atomic create-new semantics close the old exists-check/create race.
        // A raced directory or junction returns AlreadyExists and is never opened.
        create(&path)?;
        let pinned = Self::open(&path)?;
        parent.verify()?;
        pinned.verify()?;
        Ok(pinned)
    }

    fn verify(&self) -> io::Result<()> {
        let (current, identity) = open_local_directory(&self.path)?;
        drop(current);
        if identity != self.identity {
            return Err(io::Error::new(
                io::ErrorKind::InvalidData,
                format!(
                    "benchmark directory identity changed during execution: {}",
                    self.path.display()
                ),
            ));
        }
        Ok(())
    }
}

#[cfg(windows)]
fn open_local_directory(path: &std::path::Path) -> io::Result<(File, DirectoryIdentity)> {
    let handle = OpenOptions::new()
        .read(true)
        .share_mode(FILE_SHARE_READ | FILE_SHARE_WRITE)
        .custom_flags(FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT)
        .open(path)?;
    let mut information = unsafe { std::mem::zeroed::<BY_HANDLE_FILE_INFORMATION>() };
    let opened =
        unsafe { GetFileInformationByHandle(handle.as_raw_handle() as _, &mut information) };
    if opened == 0 {
        return Err(io::Error::last_os_error());
    }
    if information.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY == 0
        || information.dwFileAttributes & FILE_ATTRIBUTE_REPARSE_POINT != 0
    {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            format!(
                "benchmark directory must be a local non-reparse directory: {}",
                path.display()
            ),
        ));
    }
    Ok((
        handle,
        DirectoryIdentity {
            volume_serial: information.dwVolumeSerialNumber,
            file_index: (u64::from(information.nFileIndexHigh) << 32)
                | u64::from(information.nFileIndexLow),
        },
    ))
}

fn main() {
    if let Err(error) = run() {
        eprintln!("catalog benchmark failed: {error}");
        std::process::exit(1);
    }
}

fn run() -> Result<(), Box<dyn std::error::Error>> {
    let mut target = None;
    let mut warmup_commits = REFERENCE_WARMUP_COMMITS;
    let mut measured_commits = REFERENCE_MEASURED_COMMITS;
    let mut args = env::args().skip(1);
    while let Some(argument) = args.next() {
        match argument.as_str() {
            "--target" => target = args.next().map(PathBuf::from),
            "--warmups" => warmup_commits = parse_count(args.next(), "--warmups")?,
            "--samples" => measured_commits = parse_count(args.next(), "--samples")?,
            _ => return Err(format!("unknown argument: {argument}").into()),
        }
    }
    let target = target.ok_or("--target is required")?;
    fs::create_dir_all(&target)?;

    #[cfg(windows)]
    let target_pin = PinnedDirectory::open(&target)?;
    #[cfg(windows)]
    let mut fixture_pins = Vec::new();

    let config = BenchmarkRunConfig {
        warmup_commits,
        measured_commits,
    };
    let mut reports = Vec::new();
    for fixture in build_fixture_matrix()? {
        let fixture_root = target.join(fixture.kind().name());
        #[cfg(windows)]
        let fixture_pin = PinnedDirectory::create_child(&target_pin, fixture.kind().name())?;
        #[cfg(not(windows))]
        if fixture_root.exists() {
            return Err(format!(
                "fixture target already exists; refuse to mix benchmark runs: {}",
                fixture_root.display()
            )
            .into());
        }
        reports.push(run_fixture_benchmark(&fixture_root, &fixture, config)?);
        #[cfg(windows)]
        {
            fixture_pin.verify()?;
            target_pin.verify()?;
            fixture_pins.push(fixture_pin);
        }
    }

    #[cfg(windows)]
    {
        target_pin.verify()?;
        for fixture_pin in &fixture_pins {
            fixture_pin.verify()?;
        }
    }

    let report = RunnerReport {
        schema_version: 1,
        warmup_commits,
        measured_commits,
        fixtures: reports,
    };
    println!("{}", serde_json::to_string(&report)?);
    Ok(())
}

fn parse_count(value: Option<String>, argument: &str) -> Result<usize, Box<dyn std::error::Error>> {
    let value = value.ok_or_else(|| format!("{argument} requires a value"))?;
    Ok(value.parse()?)
}

#[cfg(all(test, windows))]
mod tests {
    use super::PinnedDirectory;
    use std::fs;
    use std::process::{Command, Stdio};

    #[test]
    fn pinned_fixture_root_cannot_be_replaced_while_the_benchmark_uses_it() {
        let target =
            std::env::temp_dir().join(format!("anima-corefs-benchmark-pin-{}", std::process::id()));
        let detached = target.with_extension("detached");
        let _ = fs::remove_dir_all(&target);
        let _ = fs::remove_dir_all(&detached);
        fs::create_dir_all(&target).unwrap();

        let target_pin = PinnedDirectory::open(&target).unwrap();
        let fixture_pin = PinnedDirectory::create_child(&target_pin, "medium").unwrap();
        let error = fs::rename(target.join("medium"), &detached).unwrap_err();

        assert!(matches!(error.raw_os_error(), Some(5) | Some(32)));
        fixture_pin.verify().unwrap();

        drop(fixture_pin);
        fs::rename(target.join("medium"), &detached).unwrap();
        drop(target_pin);
        fs::remove_dir_all(&target).unwrap();
        fs::remove_dir_all(&detached).unwrap();
    }

    #[test]
    fn fixture_root_pin_rejects_a_reparse_swap_between_create_and_open() {
        let target = std::env::temp_dir().join(format!(
            "anima-corefs-benchmark-pin-race-{}",
            std::process::id()
        ));
        let outside = target.with_extension("outside");
        let _ = fs::remove_dir_all(&target);
        let _ = fs::remove_dir_all(&outside);
        fs::create_dir_all(&target).unwrap();
        fs::create_dir_all(&outside).unwrap();
        let target_pin = PinnedDirectory::open(&target).unwrap();

        let outcome = PinnedDirectory::create_child_with(&target_pin, "medium", |path| {
            fs::create_dir(path)?;
            fs::remove_dir(path)?;
            let status = Command::new("cmd")
                .args(["/c", "mklink", "/J"])
                .arg(path)
                .arg(&outside)
                .stdout(Stdio::null())
                .stderr(Stdio::null())
                .status()?;
            if !status.success() {
                return Err(std::io::Error::other("could not create test junction"));
            }
            Ok(())
        });

        let error = match outcome {
            Ok(_) => panic!("raced reparse fixture root was accepted"),
            Err(error) => error,
        };
        assert_eq!(error.kind(), std::io::ErrorKind::InvalidData);
        assert!(!outside.join("fs").exists());

        drop(target_pin);
        fs::remove_dir_all(&target).unwrap();
        fs::remove_dir_all(&outside).unwrap();
    }
}
