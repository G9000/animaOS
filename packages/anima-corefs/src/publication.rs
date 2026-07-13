use getrandom::getrandom;
use std::fs::{File, OpenOptions};
use std::io::{self, Write};
use std::path::{Path, PathBuf};

#[cfg(unix)]
use std::os::unix::fs::OpenOptionsExt;

/// Atomically publish `payload` and return only after its directory entry is durable.
pub fn atomic_publish(target: &Path, payload: &[u8]) -> io::Result<()> {
    let parent = target.parent().ok_or_else(|| {
        io::Error::new(
            io::ErrorKind::InvalidInput,
            "publication target has no parent",
        )
    })?;
    let (mut temporary, temporary_path) = create_temporary(target)?;

    let result = (|| {
        temporary.write_all(payload)?;
        temporary.sync_all()?;
        drop(temporary);

        replace_durably(&temporary_path, target)?;
        sync_parent(parent)?;
        Ok(())
    })();

    if result.is_err() {
        let _ = std::fs::remove_file(&temporary_path);
    }
    result
}

fn create_temporary(target: &Path) -> io::Result<(File, PathBuf)> {
    let parent = target.parent().ok_or_else(|| {
        io::Error::new(
            io::ErrorKind::InvalidInput,
            "publication target has no parent",
        )
    })?;
    let target_name = target.file_name().ok_or_else(|| {
        io::Error::new(
            io::ErrorKind::InvalidInput,
            "publication target has no file name",
        )
    })?;

    for _ in 0..16 {
        let mut random = [0_u8; 8];
        getrandom(&mut random).map_err(io::Error::other)?;
        let suffix = u64::from_ne_bytes(random);
        let temporary_path =
            parent.join(format!(".{}.{}.tmp", target_name.to_string_lossy(), suffix));
        let mut options = OpenOptions::new();
        options.write(true).create_new(true);
        #[cfg(unix)]
        options.mode(0o600);
        match options.open(&temporary_path) {
            Ok(file) => return Ok((file, temporary_path)),
            Err(error) if error.kind() == io::ErrorKind::AlreadyExists => continue,
            Err(error) => return Err(error),
        }
    }

    Err(io::Error::new(
        io::ErrorKind::AlreadyExists,
        "could not allocate a unique publication file",
    ))
}

#[cfg(not(windows))]
fn replace_durably(temporary: &Path, target: &Path) -> io::Result<()> {
    std::fs::rename(temporary, target)
}

#[cfg(windows)]
fn replace_durably(temporary: &Path, target: &Path) -> io::Result<()> {
    use std::os::windows::ffi::OsStrExt;
    use windows_sys::Win32::Storage::FileSystem::{
        MoveFileExW, MOVEFILE_REPLACE_EXISTING, MOVEFILE_WRITE_THROUGH,
    };

    let source: Vec<u16> = temporary.as_os_str().encode_wide().chain(Some(0)).collect();
    let destination: Vec<u16> = target.as_os_str().encode_wide().chain(Some(0)).collect();
    let replaced = unsafe {
        MoveFileExW(
            source.as_ptr(),
            destination.as_ptr(),
            MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH,
        )
    };
    if replaced == 0 {
        return Err(io::Error::last_os_error());
    }
    Ok(())
}

#[cfg(not(windows))]
fn sync_parent(parent: &Path) -> io::Result<()> {
    File::open(parent)?.sync_all()
}

#[cfg(windows)]
fn sync_parent(_parent: &Path) -> io::Result<()> {
    // Windows does not support fsync on directory handles. MOVEFILE_WRITE_THROUGH
    // above is its documented durability primitive for the completed replacement.
    Ok(())
}
