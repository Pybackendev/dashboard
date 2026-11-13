# Store analytics

## Product
Ukrainian-language analytics for an OpenCart shop owner. The opening view prioritizes revenue, orders, average order value and products with low stock. Existing charts, order details and API contracts define the data behavior.

## Visual direction
Quiet, precise financial reporting. Cool light surfaces and deep graphite dark surfaces share a blue accent. Spacious header, aligned tabular numbers and restrained borders establish hierarchy. Revenue has a blue top rule. Zero stock is labeled with its quantity and emphasized in red.

## Tokens and ownership
The CSS variables in `frontend/index.html` are the canonical tokens. `:root` owns the light palette and `[data-theme="dark"]` overrides it. `applyTheme` reads these tokens into Chart.js, updating existing charts without fetching data or changing filters.

Light page/surface/ink/accent are #f1f4f8, #ffffff, #15273d, #2563cf. Dark values are #0e1520, #172130, #eef3fc, #76a9ff. Typography follows the existing system-font approach, prioritizing Segoe UI on Windows. Cards use a 16px radius.

## Theme behavior
The initial theme follows the operating system. Explicit light/dark selection persists in `dashboardTheme`. Storage failures retain an in-memory selection. OS changes apply while no explicit preference exists. Theme initialization precedes paint.

## Component ownership
Existing `.btn`, `.card`, `.seg`, `.pill`, native date/select controls, `toast` and the order `dialog` remain shared presentation owners. Native inputs retain platform-owned popups. Theme controls use a native button with a fixed accessible name and dark-theme pressed state. Ukrainian text and Kyiv date formatting remain canonical.

## Responsive behavior and verification
Desktop displays four metric cards. Tablet uses two columns. Below 760px, revenue and stock span the available width and chart rows stack. Focus must be visible in both themes. The verification harness `../work/check-dashboard.cjs` exercises Chromium with isolated fixture responses and writes desktop/mobile previews. Backend and live integration verification are separate.
