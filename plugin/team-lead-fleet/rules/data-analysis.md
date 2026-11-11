# Analysis of data: which skill fires when

The method lives in the skills. This rule is only what must fire before you would think to open one. Bryan: *"It's been a struggle getting valid data and estimating what research is high quality, relevant, and then applying it correctly."*

- **Before an in-depth analysis of outside data** (more than about two hours, or the result will be sent or published): run `prior-work`. Not for debugging, the fleet's own systems, or `product-discovery` questions.
- **Before a dataset's first figure goes onto a page or out of the session:** run `dataset-intake`. A quick chat answer can come first, with a caveat (`eda`).
- **Before a model or doc applies a study's effect or cost, or a local series sets an effect size:** run `grade-evidence`. Never size an effect from a local series whose interval spans zero or is wide, and never use one end of an interval as a point estimate.
- **Before figures go to Bryan for sign-off or outside the session:** run `pre-send`. Put each number beside the published or earlier figure for the same quantity, in absolute values and not only ratios. Explain any gap over about 1.5x before you hand it over. The reviewer should never be first to spot that the parts disagree.
- **A quick exploratory question:** `eda`. **A page, chart or report built on numbers:** `data-page`.
