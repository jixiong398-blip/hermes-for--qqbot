# SPA JavaScript Logic Extraction

When browser automation is unavailable (Playwright/Puppeteer not supported, or headless environment), you can still obtain computed results from single-page applications by extracting and re-implementing their client-side logic.

## Technique

1. **Fetch the page source** with `mcp_anysearch_extract` or `curl -sL <url>`.
2. **Read the inline `<script>` blocks** — many SPAs embed their entire question set, dimension definitions, scoring algorithm, and type profiles in JavaScript directly in the HTML.
3. **Re-implement the scoring logic** in Python (or any language) by:
   - Copying the question/answer data structures
   - Replicating the normalization and distance calculation functions
   - Feeding your answers through the local simulation

## Common Patterns in Test/Quiz SPAs

| Pattern | What to look for |
|---------|-----------------|
| **Questions array** | `const questions=[...]` with `[id, text, {dim:weight}]` tuples |
| **Dimension definitions** | `const dimensions={key:"label"}` mapping keys to display names |
| **Type profiles** | `const typeProfiles=[...]` with target scores, weights, tags |
| **Scoring function** | Centered scoring: answer - 4 (midpoint), multiplied by weights, normalized to 0-100 |
| **Distance function** | Weighted Euclidean distance between computed scores and profile targets |
| **Consistency penalty** | Additional penalties for incompatible score combinations |
| **Choice function** | Lower distance → better match; confidence from distance + margin to second place |

## Example: L-Index Personality Test

The L-Index test at leindex.pages.dev embeds all logic in one HTML file (~38KB). The flow:

1. 48 questions, each mapping to 1-3 dimensions with weights (2, 1, or -1)
2. 7-point Likert scale (1=完全不符合, 4=不确定, 7=非常符合)
3. Answers centered at zero: `centered = answer - 4`
4. Raw scores: `raw[dim] += centered * weight` across all answered questions
5. Max possible: `3 * sum(|weight|)` per dimension
6. Normalized score: `50 + (raw / max_abs) * 50` clamped to 0-100
7. Type matching: weighted Euclidean distance to 14 type profiles + consistency penalty
8. 12 dimensions: dominance, caretaker, surrender, romantic, attachment, teasing, avoidance, softness, initiative, possessive, socialHeat, security

## When to Use

- The SPA embeds its logic in inline `<script>` tags (not loaded dynamically via JS bundles)
- You have the answers you want to test (character's personality, hypothetical scenarios)
- Browser automation is unavailable or too slow for the task
- The scoring algorithm is deterministic (no randomness or server-side computation)

## Pitfalls

- **Scattered logic**: Some SPAs split data across multiple script tags or fetch JSON async. Check network requests or look for `fetch()` calls.
- **Obfuscation**: Minified/uglified JS may use single-letter variable names. Search for key phrases like "questions", "dimensions", "typeProfiles" or look for `[id, text, scores]` tuple patterns.
- **Randomized results**: Some tests include random noise or server-side components that can't be replicated locally.
- **Negative weights**: Some dimensions have negative weights (e.g., `softness: -1` means high agreement lowers the softness score). Include both positive and negative in your implementation.
