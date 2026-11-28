//! Deterministic SplitMix64 PRNG for reproducible A/B differential parity
//! tests between the legacy Python implementation and the Safe Rust port.

/// Deterministic 64-bit SplitMix64 pseudo-random number generator.
///
/// Produces bit-for-bit identical sequences to the Python `SplitMix64`
/// reference used in the A/B differential parity harness.
///
/// # Examples
/// ```
/// use banido_core::prng::SplitMix64;
///
/// let mut rng = SplitMix64::new(42);
/// let first = rng.next_u64();
/// let mut rng2 = SplitMix64::new(42);
/// assert_eq!(first, rng2.next_u64());
/// let u = rng.next_f64();
/// assert!((0.0..1.0).contains(&u));
/// ```
#[derive(Debug, Clone, Copy)]
pub struct SplitMix64 {
    state: u64,
}

impl SplitMix64 {
    /// Creates a new SplitMix64 generator initialized with `seed`.
    #[inline]
    pub const fn new(seed: u64) -> Self {
        Self { state: seed }
    }

    /// Advances the generator and returns the next uniformly distributed `u64`.
    #[inline]
    pub fn next_u64(&mut self) -> u64 {
        self.state = self.state.wrapping_add(0x9E37_79B9_7F4A_7C15);
        let mut z = self.state;
        z = (z ^ (z >> 30)).wrapping_mul(0xBF58_476D_1CE4_E5B9);
        z = (z ^ (z >> 27)).wrapping_mul(0x94D0_49BB_1331_11EB);
        z ^ (z >> 31)
    }

    /// Returns a uniform `f64` in `[0.0, 1.0)` using the top 53 bits.
    #[inline]
    pub fn next_f64(&mut self) -> f64 {
        let bits = self.next_u64() >> 11;
        (bits as f64) * (1.0 / ((1u64 << 53) as f64))
    }

    /// Returns a uniform `f32` in `[-1.0, 1.0)`.
    #[inline]
    pub fn next_signed_f32(&mut self) -> f32 {
        (self.next_f64() * 2.0 - 1.0) as f32
    }
}
