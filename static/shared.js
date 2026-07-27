// Shared formatting helpers used by both index.html (customer screen)
// and admin.html (demo control screen). Plain script tag, no build
// step, no module system -- consistent with CLAUDE.md's "no build
// step, ever" for the frontend. Kept intentionally tiny: only the
// formatters both pages actually need twice, not a general utility
// library.

function formatCurrency(amount) {
  if (amount === null || amount === undefined) return '';
  return new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(amount);
}

function formatDate(isoDate) {
  if (!isoDate) return '';
  const d = new Date(isoDate + 'T00:00:00');
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
}

function formatDateTime(isoDateTime) {
  if (!isoDateTime) return '';
  const d = new Date(isoDateTime);
  return d.toLocaleString('en-US', { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
}
