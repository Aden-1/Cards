# Mobile optimization plan

## Guardrails

- Preserve every existing route, form submission, fetch request, game rule, and
  component structure.
- Prefer progressive CSS and browser feature fallbacks so desktop behavior and
  older mobile browsers remain usable.
- Validate the full existing test suite after changes; this is a presentation
  and client-scheduling optimization, not a data or API change.

## Implemented work

1. Improve mobile delivery and viewport behavior with a `viewport-fit=cover`
   viewport declaration, safe scroll offsets, and mobile-safe tap highlighting.
2. Reduce mobile rendering cost by disabling the fixed background texture and
   header blur on narrow screens while retaining the same surfaces, colors, and
   layout hierarchy.
3. Improve thumb usability by giving compact navigation, action, and tag
   controls a 44px minimum touch target on narrow screens.
4. Prevent hover transforms from sticking on touch devices and respect
   `prefers-reduced-motion` for users who request less animation.
5. Throttle the mobile header scroll work to one `requestAnimationFrame` per
   frame, and add a MediaQueryList fallback for older Safari.
6. Make theme initialization resilient when mobile privacy settings block
   `localStorage`; the chosen theme still applies for the current session.

## Verification

- Run the existing Python test suite and static asset audit.
- Review the diff to confirm no route, model, API, or component interaction
  logic changed.
- Manually smoke-test narrow viewport navigation, dropdowns, forms, study,
  mastery, match, reorder, quiz, theme switching, and toast feedback.

## Follow-up measurement

After deployment, compare mobile Lighthouse/Web Vitals before and after,
especially First Contentful Paint, Interaction to Next Paint, Cumulative Layout
Shift, and total blocking time. Only consider bundle splitting or removing
Bootstrap after measuring actual usage across all templates.
