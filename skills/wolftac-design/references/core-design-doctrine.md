# Core design doctrine

The brand-independent quality bar. This is what "Wolftac doctrine as internal standard,
not visual default" means concretely: these rules apply to a Wolftac artifact, an OMAS
artifact, and a neutral one alike. None of them names a colour.

## Hierarchy

- One primary message per view. If two things are the largest, neither is.
- Three type sizes carry a page: display, body, and label. A fourth needs a reason.
- Weight and size do the ranking; colour reinforces, never replaces it. A hierarchy that
  collapses in greyscale is not a hierarchy.
- Section labels are the smallest, most-spaced text on the page and the highest-contrast
  accent. That inversion is what makes a dense layout navigable.

## Spacing rhythm

- Pick one base unit (8px is the common choice) and derive every gap from it. Arbitrary
  spacing reads as carelessness even when nobody can name why.
- Space between modules is generous; space within a module is tight. The contrast
  between those two is what groups content — borders are the fallback, not the tool.
- Vertical rhythm beats horizontal decoration. A page with one column and correct
  spacing outperforms a grid with none.

## Type scale

- Body copy sits at 16px minimum on web, with a measure of 55-75 characters.
- Line height falls as size rises: ~1.6 for body, ~1.1 for display.
- Tabular numerals for anything a reader compares down a column.
- Uppercase is a label treatment, never a paragraph treatment. It needs letter-spacing;
  lowercase does not.

## Contrast

- Body copy holds WCAG AA at its rendered size. Measure it; do not estimate it.
- Never encode meaning in colour alone. A status chip carries a word.
- A focus state is designed, not removed. It must be visible against both the rest and
  hover grounds — those are two different measurements.

## Density

- Density is earned by alignment. Tight spacing over ragged edges reads as clutter;
  tight spacing over exact alignment reads as engineered.
- Every element sits on a shared edge with something else. If it aligns with nothing, it
  is decoration.
- Empty space is structural. Removing it to "fit more" costs more than it gains.

## Restraint

- One atmospheric effect per page, at most. Two is a theme; three is a toy.
- Effects go behind content, never on it.
- Motion is functional and under 150ms, and every transition honours
  `prefers-reduced-motion: reduce`.
- If a decorative element cannot survive the question "what does this tell the reader?",
  it does not ship.

## States are first-class

Empty, loading, and error states get the same visual grade as the populated one. A
designed empty state is the difference between "no records for this filter" and a page
that looks broken.
