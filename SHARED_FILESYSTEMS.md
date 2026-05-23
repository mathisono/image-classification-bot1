# Shared / Synchronized Filesystem Roots

Image Librarian is designed to scan the path as it exists on the machine running the app. Do not hardcode another machine's absolute path into the Python code.

For example, if one computer sees the camera archive as:

```text
/home/kj6dzb/2/MSE-87/Cam_Now
```

but another computer sees the same synchronized filesystem as:

```text
/MSE-87/Cam_Now
```

or:

```text
/mnt/MSE-87/Cam_Now
```

the app should be configured with the root path for the current machine.

## Recommended config

Use a named root in `config.yaml`:

```yaml
image_roots:
  - name: "MSE-87"
    path: "/mnt/MSE-87"
    shared: true
    follow_symlinks: false
    enabled: true
```

Then the scanner stores both:

```text
absolute path: /mnt/MSE-87/Cam_Now/example.jpg
root name:     MSE-87
relative path: Cam_Now/example.jpg
```

This makes the database easier to understand when the same shared filesystem is visible at different paths on different computers.

## GUI path input

The dashboard includes a **Scan a path now** form. You can enter any mounted/shared path, for example:

```text
/MSE-87
/mnt/MSE-87
/home/kj6dzb/2/MSE-87
```

Use root name:

```text
MSE-87
```

Check the shared/synchronized filesystem box so the scanner skips files that were modified very recently. This helps avoid trying to index an image while another computer is still writing it.

## Why shared=true matters

When `shared: true`, the scanner applies a stability delay:

```yaml
scanner:
  shared_fs_stability_seconds: 5
```

Files modified in the last few seconds are skipped until a later scan. This is useful for SMB shares, Syncthing-style folders, camera ingest folders, and other network/synchronized filesystems.

## Missing source files

If a file exists in the database but disappears before processing, the app marks it:

```text
MISSING_SOURCE
```

This can happen when:

- the SMB share disconnects
- another computer moves or deletes the file
- the synchronized filesystem has not finished syncing
- the local mount path changed

The original file is not modified by the app.
