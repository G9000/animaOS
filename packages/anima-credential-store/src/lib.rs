//! Fail-closed operating-system credential storage shared by native ANIMA hosts.

use sha2::{Digest, Sha256};
use std::{fs, io, path::Path, sync::Mutex};
use thiserror::Error;

const SERVICE_NAME: &str = "com.anima.credentials.v1";
const REFERENCE_PREFIX: &str = "anima-credential:v1:";

#[derive(Debug, Error)]
pub enum CredentialStoreError {
    #[error("invalid credential reference")]
    InvalidReference,
    #[error("credential values must be non-empty")]
    EmptySecret,
    #[error("operating-system credential support is unavailable: {0}")]
    Unavailable(String),
    #[error("operating-system credential write verification failed")]
    Verification,
    #[error("legacy credential conflicts with the operating-system credential")]
    LegacyConflict,
    #[error("legacy credential migration failed: {0}")]
    LegacyIo(#[from] io::Error),
}

pub trait CredentialBackend: Send + Sync {
    fn get(&self, reference: &str) -> Result<Option<String>, CredentialStoreError>;
    fn put(&self, reference: &str, secret: &str) -> Result<(), CredentialStoreError>;
    fn delete(&self, reference: &str) -> Result<(), CredentialStoreError>;
}

#[derive(Default)]
pub struct OsCredentialBackend;

impl OsCredentialBackend {
    pub fn new() -> Result<Self, CredentialStoreError> {
        #[cfg(not(any(target_os = "linux", target_os = "macos", windows)))]
        return Err(CredentialStoreError::Unavailable(
            "this operating system has no approved credential adapter".to_owned(),
        ));
        #[cfg(any(target_os = "linux", target_os = "macos", windows))]
        Ok(Self)
    }

    fn entry(reference: &str) -> Result<keyring::Entry, CredentialStoreError> {
        validate_credential_reference(reference)?;
        keyring::Entry::new(SERVICE_NAME, reference)
            .map_err(|error| CredentialStoreError::Unavailable(error.to_string()))
    }
}

impl CredentialBackend for OsCredentialBackend {
    fn get(&self, reference: &str) -> Result<Option<String>, CredentialStoreError> {
        match Self::entry(reference)?.get_password() {
            Ok(secret) => Ok(Some(secret)),
            Err(keyring::Error::NoEntry) => Ok(None),
            Err(error) => Err(CredentialStoreError::Unavailable(error.to_string())),
        }
    }

    fn put(&self, reference: &str, secret: &str) -> Result<(), CredentialStoreError> {
        if secret.is_empty() {
            return Err(CredentialStoreError::EmptySecret);
        }
        Self::entry(reference)?
            .set_password(secret)
            .map_err(|error| CredentialStoreError::Unavailable(error.to_string()))?;
        if self.get(reference)?.as_deref() != Some(secret) {
            return Err(CredentialStoreError::Verification);
        }
        Ok(())
    }

    fn delete(&self, reference: &str) -> Result<(), CredentialStoreError> {
        match Self::entry(reference)?.delete_credential() {
            Ok(()) | Err(keyring::Error::NoEntry) => {}
            Err(error) => return Err(CredentialStoreError::Unavailable(error.to_string())),
        }
        if self.get(reference)?.is_some() {
            return Err(CredentialStoreError::Verification);
        }
        Ok(())
    }
}

pub struct CredentialStore<B> {
    backend: B,
    operation_lock: Mutex<()>,
}

impl<B: CredentialBackend> CredentialStore<B> {
    pub fn new(backend: B) -> Self {
        Self {
            backend,
            operation_lock: Mutex::new(()),
        }
    }

    pub fn get(&self, reference: &str) -> Result<Option<String>, CredentialStoreError> {
        let _operation = self.operation_lock.lock().map_err(|_| {
            CredentialStoreError::Unavailable("credential operation lock poisoned".to_owned())
        })?;
        self.backend.get(reference)
    }

    pub fn put(&self, reference: &str, secret: &str) -> Result<(), CredentialStoreError> {
        let _operation = self.operation_lock.lock().map_err(|_| {
            CredentialStoreError::Unavailable("credential operation lock poisoned".to_owned())
        })?;
        self.backend.put(reference, secret)
    }

    pub fn delete(&self, reference: &str) -> Result<(), CredentialStoreError> {
        let _operation = self.operation_lock.lock().map_err(|_| {
            CredentialStoreError::Unavailable("credential operation lock poisoned".to_owned())
        })?;
        self.backend.delete(reference)
    }

    pub fn load_or_create_migrating_legacy(
        &self,
        reference: &str,
        legacy_path: &Path,
        supplied: Option<&str>,
        generate: impl FnOnce() -> String,
    ) -> Result<String, CredentialStoreError> {
        let _operation = self.operation_lock.lock().map_err(|_| {
            CredentialStoreError::Unavailable("credential operation lock poisoned".to_owned())
        })?;
        validate_credential_reference(reference)?;
        let legacy = read_legacy_secret(legacy_path)?;
        let stored = self.backend.get(reference)?;

        let selected = if let Some(supplied) = supplied.filter(|value| !value.trim().is_empty()) {
            let selected = supplied.trim().to_owned();
            self.backend.put(reference, &selected)?;
            selected
        } else if let Some(stored) = stored {
            if let Some(legacy) = legacy.as_deref() {
                if legacy != stored {
                    return Err(CredentialStoreError::LegacyConflict);
                }
            }
            stored
        } else if let Some(legacy) = legacy {
            self.backend.put(reference, &legacy)?;
            legacy
        } else {
            let generated = generate();
            if generated.is_empty() {
                return Err(CredentialStoreError::EmptySecret);
            }
            self.backend.put(reference, &generated)?;
            generated
        };

        if self.backend.get(reference)?.as_deref() != Some(selected.as_str()) {
            return Err(CredentialStoreError::Verification);
        }
        remove_legacy_secret(legacy_path)?;
        Ok(selected)
    }

    pub fn load_migrating_legacy(
        &self,
        reference: &str,
        legacy_path: &Path,
    ) -> Result<Option<String>, CredentialStoreError> {
        let _operation = self.operation_lock.lock().map_err(|_| {
            CredentialStoreError::Unavailable("credential operation lock poisoned".to_owned())
        })?;
        validate_credential_reference(reference)?;
        let legacy = read_legacy_secret(legacy_path)?;
        let stored = self.backend.get(reference)?;
        let selected = match (stored, legacy) {
            (Some(stored), Some(legacy)) if stored != legacy => {
                return Err(CredentialStoreError::LegacyConflict)
            }
            (Some(stored), _) => Some(stored),
            (None, Some(legacy)) => {
                self.backend.put(reference, &legacy)?;
                Some(legacy)
            }
            (None, None) => None,
        };
        if let Some(selected) = selected.as_deref() {
            if self.backend.get(reference)?.as_deref() != Some(selected) {
                return Err(CredentialStoreError::Verification);
            }
            remove_legacy_secret(legacy_path)?;
        }
        Ok(selected)
    }

    pub fn import_legacy_value(
        &self,
        reference: &str,
        legacy_secret: &str,
    ) -> Result<String, CredentialStoreError> {
        let _operation = self.operation_lock.lock().map_err(|_| {
            CredentialStoreError::Unavailable("credential operation lock poisoned".to_owned())
        })?;
        validate_credential_reference(reference)?;
        let legacy_secret = legacy_secret.trim();
        if legacy_secret.is_empty() {
            return Err(CredentialStoreError::EmptySecret);
        }
        if let Some(stored) = self.backend.get(reference)? {
            if stored != legacy_secret {
                return Err(CredentialStoreError::LegacyConflict);
            }
            return Ok(stored);
        }
        self.backend.put(reference, legacy_secret)?;
        if self.backend.get(reference)?.as_deref() != Some(legacy_secret) {
            return Err(CredentialStoreError::Verification);
        }
        Ok(legacy_secret.to_owned())
    }
}

pub fn credential_reference(scope: &str, name: &str) -> Result<String, CredentialStoreError> {
    if !valid_component(scope) || !valid_component(name) {
        return Err(CredentialStoreError::InvalidReference);
    }
    let mut digest = Sha256::new();
    digest.update(b"anima-credential-reference-v1\0");
    for component in [scope, name] {
        let encoded = component.as_bytes();
        digest.update((encoded.len() as u32).to_be_bytes());
        digest.update(encoded);
    }
    Ok(format!(
        "{REFERENCE_PREFIX}{}",
        hex::encode(digest.finalize())
    ))
}

pub fn validate_credential_reference(reference: &str) -> Result<(), CredentialStoreError> {
    let Some(digest) = reference.strip_prefix(REFERENCE_PREFIX) else {
        return Err(CredentialStoreError::InvalidReference);
    };
    if digest.len() != 64
        || !digest
            .bytes()
            .all(|byte| byte.is_ascii_digit() || matches!(byte, b'a'..=b'f'))
    {
        return Err(CredentialStoreError::InvalidReference);
    }
    Ok(())
}

fn valid_component(component: &str) -> bool {
    !component.is_empty()
        && component.len() <= 128
        && component.bytes().enumerate().all(|(index, byte)| {
            byte.is_ascii_alphanumeric() || (index > 0 && matches!(byte, b'.' | b'_' | b':' | b'-'))
        })
}

fn read_legacy_secret(path: &Path) -> Result<Option<String>, CredentialStoreError> {
    match fs::read_to_string(path) {
        Ok(value) => {
            let trimmed = value.trim();
            Ok((!trimmed.is_empty()).then(|| trimmed.to_owned()))
        }
        Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(None),
        Err(error) => Err(error.into()),
    }
}

fn remove_legacy_secret(path: &Path) -> Result<(), CredentialStoreError> {
    match fs::remove_file(path) {
        Ok(()) => sync_parent(path),
        Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(()),
        Err(error) => Err(error.into()),
    }
}

#[cfg(unix)]
fn sync_parent(path: &Path) -> Result<(), CredentialStoreError> {
    if let Some(parent) = path.parent() {
        fs::File::open(parent)?.sync_all()?;
    }
    Ok(())
}

#[cfg(not(unix))]
fn sync_parent(_path: &Path) -> Result<(), CredentialStoreError> {
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::HashMap;

    #[derive(Default)]
    struct MemoryBackend(Mutex<HashMap<String, String>>);

    impl CredentialBackend for MemoryBackend {
        fn get(&self, reference: &str) -> Result<Option<String>, CredentialStoreError> {
            Ok(self.0.lock().unwrap().get(reference).cloned())
        }

        fn put(&self, reference: &str, secret: &str) -> Result<(), CredentialStoreError> {
            self.0
                .lock()
                .unwrap()
                .insert(reference.to_owned(), secret.to_owned());
            Ok(())
        }

        fn delete(&self, reference: &str) -> Result<(), CredentialStoreError> {
            self.0.lock().unwrap().remove(reference);
            Ok(())
        }
    }

    #[test]
    fn canonical_reference_matches_python_contract() {
        assert_eq!(
            credential_reference("daemon", "control-token").unwrap(),
            "anima-credential:v1:3b4a876a9ca4c01210fc9397f433d17cf9f00132b57d7aea7919e144ad0c04cc"
        );
    }

    #[test]
    fn legacy_migration_copy_verifies_then_removes_plaintext() {
        let root = tempfile::tempdir().unwrap();
        let legacy = root.path().join("runtime-daemon.control-token");
        fs::write(&legacy, "legacy-private-token\n").unwrap();
        let reference = credential_reference("daemon", "control-token").unwrap();
        let store = CredentialStore::new(MemoryBackend::default());

        assert_eq!(
            store
                .load_or_create_migrating_legacy(&reference, &legacy, None, || {
                    "generated".to_owned()
                })
                .unwrap(),
            "legacy-private-token"
        );
        assert!(!legacy.exists());
        assert_eq!(
            store.get(&reference).unwrap().as_deref(),
            Some("legacy-private-token")
        );
    }

    #[test]
    fn conflicting_legacy_and_secure_values_fail_closed_without_deletion() {
        let root = tempfile::tempdir().unwrap();
        let legacy = root.path().join("runtime-daemon.control-token");
        fs::write(&legacy, "legacy-private-token\n").unwrap();
        let reference = credential_reference("daemon", "control-token").unwrap();
        let store = CredentialStore::new(MemoryBackend::default());
        store.put(&reference, "secure-private-token").unwrap();

        assert!(matches!(
            store.load_or_create_migrating_legacy(&reference, &legacy, None, || {
                "generated".to_owned()
            }),
            Err(CredentialStoreError::LegacyConflict)
        ));
        assert!(legacy.exists());
    }
}
