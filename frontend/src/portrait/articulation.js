const clamp = value => Math.max(0, Math.min(1, value));
const REST = { open: 0, round: 0, wide: 0 };

export function mouthShape(level, decibels, binHz) {
  if (level < 0.065) return REST;
  let bass = 0, jaw = 0, mids = 0, weightedMids = 0, hiss = 0;
  // Relative band energy separates low, wide and sibilant sounds without a network model.
  for (let i = Math.ceil(180 / binHz); i < decibels.length && i * binHz < 6100; i++) {
    const hz = i * binHz;
    const energy = 10 ** (decibels[i] / 10);
    if (hz < 490) bass += energy;
    else if (hz < 1050) jaw += energy;
    else if (hz < 3050) {
      mids += energy;
      weightedMids += hz * energy;
    } else hiss += energy;
  }
  const middle = mids > 1e-9 ? weightedMids / mids : 1700;
  const jawRatio = jaw / (jaw + bass + 1e-9);
  const frication = hiss / (bass + jaw + mids + hiss + 1e-9);
  const amplitude = clamp((level - 0.065) / 0.57) ** 0.66;
  return {
    open: amplitude * (0.55 + 0.45 * clamp(jawRatio * 1.65)) * (1 - 0.56 * frication),
    round: clamp((1790 - middle) / 490) * 0.91,
    wide: Math.max(clamp((middle - 1810) / 710), frication) * 0.88,
  };
}
