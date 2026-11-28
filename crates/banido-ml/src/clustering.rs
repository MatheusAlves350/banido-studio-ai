//! Native Safe Rust UPGMA (average-linkage) agglomerative clustering over
//! cosine distance on contiguous Structure-of-Arrays (SoA) speaker embeddings.
//!
//! Replaces `sklearn.cluster.AgglomerativeClustering(n_clusters=None,
//! metric="cosine", linkage="average", distance_threshold=0.68)` with zero
//! Python/SciPy/scikit-learn dependencies and auto-vectorized dot products.

/// Contiguous Structure-of-Arrays storage for `n` fixed-dimension L2-normalized
/// speaker embeddings (192-D for SpeechBrain ECAPA-TDNN).
#[derive(Debug, Clone)]
pub struct EmbeddingMatrixSoA {
    /// Flattened row-major `f32` storage of length `count * dim`.
    data: Vec<f32>,
    /// Number of embeddings stored.
    count: usize,
    /// Dimensionality of each embedding (e.g. `192`).
    dim: usize,
}

impl EmbeddingMatrixSoA {
    /// Packs a slice of `f32` slices into a contiguous L2-normalized matrix.
    pub fn from_vecs(vecs: &[Vec<f32>]) -> Self {
        let count = vecs.len();
        let dim = vecs.first().map(Vec::len).unwrap_or(0);
        let mut data = Vec::with_capacity(count * dim);
        for v in vecs {
            let norm = l2_norm(v);
            if norm > 0.0 {
                let inv = (1.0 / norm) as f32;
                for &x in v {
                    data.push(x * inv);
                }
            } else {
                data.extend(std::iter::repeat_n(0.0_f32, dim));
            }
        }
        Self { data, count, dim }
    }

    /// Number of embeddings in the matrix.
    #[inline]
    pub fn len(&self) -> usize {
        self.count
    }

    /// Returns `true` when the matrix holds zero embeddings.
    #[inline]
    pub fn is_empty(&self) -> bool {
        self.count == 0
    }

    /// Embedding dimensionality.
    #[inline]
    pub fn dim(&self) -> usize {
        self.dim
    }

    /// Returns the `i`-th normalized embedding slice.
    #[inline]
    pub fn row(&self, i: usize) -> &[f32] {
        let s = i * self.dim;
        &self.data[s..s + self.dim]
    }
}

/// Computes the L2 norm of a slice using `f64` accumulation.
#[inline]
pub fn l2_norm(v: &[f32]) -> f64 {
    let mut sum = 0.0_f64;
    for &x in v {
        let xf = x as f64;
        sum += xf * xf;
    }
    sum.sqrt()
}

/// Computes the dot product of two equal-length `f32` slices using `f64`
/// accumulation (auto-vectorizes cleanly with LLVM AVX2/NEON/SSE).
#[inline]
pub fn dot_f32(a: &[f32], b: &[f32]) -> f64 {
    let mut acc = 0.0_f64;
    for (&x, &y) in a.iter().zip(b.iter()) {
        acc += (x as f64) * (y as f64);
    }
    acc
}

/// Computes cosine distance `1.0 - dot(u, v)` clamped to `[0.0, 2.0]` for unit vectors.
#[inline]
pub fn cosine_distance_unit(a: &[f32], b: &[f32]) -> f64 {
    (1.0 - dot_f32(a, b)).clamp(0.0, 2.0)
}

/// Runs UPGMA (average-linkage) hierarchical agglomerative clustering over
/// cosine distance, merging pairs strictly below `distance_threshold`.
///
/// Returns cluster label per item (`0..num_clusters`). Matches
/// `sklearn.cluster.AgglomerativeClustering(n_clusters=None, metric="cosine",
/// linkage="average", distance_threshold=...)`.
///
/// # Examples
/// ```
/// use banido_ml::clustering::{agglomerative_cosine_average, EmbeddingMatrixSoA};
///
/// let vecs = vec![
///     vec![1.0, 0.0, 0.0],
///     vec![0.99, 0.1, 0.0],
///     vec![0.0, 1.0, 0.0],
///     vec![0.05, 0.99, 0.0],
/// ];
/// let mat = EmbeddingMatrixSoA::from_vecs(&vecs);
/// let labels = agglomerative_cosine_average(&mat, 0.68);
/// assert_eq!(labels[0], labels[1]);
/// assert_eq!(labels[2], labels[3]);
/// assert_ne!(labels[0], labels[2]);
/// ```
pub fn agglomerative_cosine_average(mat: &EmbeddingMatrixSoA, distance_threshold: f64) -> Vec<usize> {
    let n = mat.len();
    if n == 0 {
        return Vec::new();
    }
    if n == 1 {
        return vec![0];
    }

    // Flat NxN symmetric distance matrix updated via Lance-Williams average linkage.
    let mut dist = vec![0.0_f64; n * n];
    for i in 0..n {
        let ri = mat.row(i);
        for j in (i + 1)..n {
            let d = cosine_distance_unit(ri, mat.row(j));
            dist[i * n + j] = d;
            dist[j * n + i] = d;
        }
    }

    let mut active = vec![true; n];
    let mut sizes = vec![1usize; n];
    let mut members: Vec<Vec<usize>> = (0..n).map(|i| vec![i]).collect();

    loop {
        let mut best_d = f64::INFINITY;
        let mut best_pair = None;
        for i in 0..n {
            if !active[i] {
                continue;
            }
            let row_off = i * n;
            for j in (i + 1)..n {
                if !active[j] {
                    continue;
                }
                let d = dist[row_off + j];
                if d < best_d {
                    best_d = d;
                    best_pair = Some((i, j));
                }
            }
        }

        let Some((ci, cj)) = best_pair else {
            break;
        };
        if best_d >= distance_threshold {
            break;
        }

        // Merge cluster cj into ci using weighted average linkage (UPGMA).
        let si = sizes[ci] as f64;
        let sj = sizes[cj] as f64;
        let denom = si + sj;

        for k in 0..n {
            if !active[k] || k == ci || k == cj {
                continue;
            }
            let d_new = (si * dist[ci * n + k] + sj * dist[cj * n + k]) / denom;
            dist[ci * n + k] = d_new;
            dist[k * n + ci] = d_new;
        }

        sizes[ci] += sizes[cj];
        let moved = std::mem::take(&mut members[cj]);
        members[ci].extend(moved);
        active[cj] = false;
    }

    let mut labels = vec![0usize; n];
    let mut next_label = 0usize;
    for i in 0..n {
        if active[i] {
            for &idx in &members[i] {
                labels[idx] = next_label;
            }
            next_label += 1;
        }
    }
    labels
}

#[cfg(test)]
mod tests {
    use super::*;
    use proptest::prelude::*;

    proptest! {
        #[test]
        fn prop_clustering_produces_valid_partition(n in 1usize..25, thresh in 0.1_f64..1.5_f64) {
            let mut vecs = Vec::with_capacity(n);
            for i in 0..n {
                let angle = (i as f32) * 0.5;
                vecs.push(vec![angle.cos(), angle.sin(), 0.25]);
            }
            let mat = EmbeddingMatrixSoA::from_vecs(&vecs);
            let labels = agglomerative_cosine_average(&mat, thresh);
            prop_assert_eq!(labels.len(), n);
            let max_label = labels.iter().copied().max().unwrap_or(0);
            prop_assert!(max_label < n);
        }
    }
}
