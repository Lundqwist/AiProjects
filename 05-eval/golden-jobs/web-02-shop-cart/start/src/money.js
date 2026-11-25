// Prices are in Swedish kronor (kr) with öre as decimals.

export function formatSEK(amount) {
  return amount.toLocaleString('sv-SE', { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + ' kr';
}
