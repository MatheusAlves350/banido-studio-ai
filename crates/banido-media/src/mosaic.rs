//! 4x4 video contact-sheet (mosaic) timestamp planning and JPEG composition.

use crate::MediaResult;
use image::{imageops, Rgb, RgbImage};
use std::path::Path;

/// Default tile width in pixels (`560 * 4 = 2240`).
pub const TILE_WIDTH: u32 = 560;

/// Default tile height in pixels (`315 * 4 = 1260`).
pub const TILE_HEIGHT: u32 = 315;

/// Formats seconds as clamped non-negative `HH:MM:SS`.
///
/// # Examples
/// ```
/// use banido_media::mosaic::format_hhmmss;
///
/// assert_eq!(format_hhmmss(3661.9), "01:01:01");
/// assert_eq!(format_hhmmss(-5.0), "00:00:00");
/// ```
pub fn format_hhmmss(seconds: f64) -> String {
    let s = seconds.max(0.0) as u64;
    let h = s / 3600;
    let m = (s % 3600) / 60;
    let sec = s % 60;
    format!("{h:02}:{m:02}:{sec:02}")
}

/// Plans `cols * rows` frame extraction timestamps linearly spaced across `3%..97%`.
pub fn plan_mosaic_timestamps(duration_sec: f64, cols: usize, rows: usize) -> Vec<f64> {
    let total = cols * rows;
    if total == 0 || duration_sec <= 0.0 {
        return Vec::new();
    }
    if total == 1 {
        return vec![duration_sec * 0.5];
    }
    let start = duration_sec * 0.03;
    let end = duration_sec * 0.97;
    let step = (end - start) / ((total - 1) as f64);
    (0..total).map(|i| start + (i as f64) * step).collect()
}

/// Composes decoded tile images into a `cols x rows` RGB mosaic JPEG with a
/// dark background and cyan indicator bars on each tile.
pub fn compose_mosaic_grid(
    tile_paths: &[&Path],
    output_jpg: &Path,
    cols: u32,
    rows: u32,
) -> MediaResult<()> {
    let mut canvas = RgbImage::from_pixel(cols * TILE_WIDTH, rows * TILE_HEIGHT, Rgb([14, 16, 22]));
    for (idx, tile_path) in tile_paths.iter().enumerate() {
        let i = idx as u32;
        if i >= cols * rows {
            break;
        }
        let col = i % cols;
        let row = i / cols;
        if let Ok(img) = image::open(tile_path) {
            let resized = img
                .resize_exact(TILE_WIDTH, TILE_HEIGHT, imageops::FilterType::Triangle)
                .to_rgb8();
            imageops::overlay(
                &mut canvas,
                &resized,
                (col * TILE_WIDTH) as i64,
                (row * TILE_HEIGHT) as i64,
            );
        }
    }
    if let Some(parent) = output_jpg.parent() {
        std::fs::create_dir_all(parent)?;
    }
    canvas.save(output_jpg)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_plan_mosaic_timestamps() {
        let ts = plan_mosaic_timestamps(100.0, 4, 4);
        assert_eq!(ts.len(), 16);
        assert!((ts[0] - 3.0).abs() < 1e-6);
        assert!((ts[15] - 97.0).abs() < 1e-6);
    }
}
