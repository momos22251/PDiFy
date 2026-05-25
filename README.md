# pdify

`pdify` is a Python package to extract clean text layout structures and cohesive figures from PDF documents (like research papers or reports), preventing fragmented crops or nested image duplication.

## Features
- **Cohesive Figure Extraction**: Groups overlapping/nested image elements into single cohesive crops.
- **Layout Reconstruction**: Reconstructs paragraphs and column spans in correct visual reading order.
- **Hyphenation & Ligatures**: Automatically resolves word hyphens and common ligatures.
- **Parallel Processing**: Uses multi-processing to extract content concurrently.

## Installation

You can install `pdify` directly from PyPI:
```bash
pip install pdify
```
*Note: Depending on your system and PDF files, you may also need `ghostscript` if rendering certain vector formats.*

## Usage

### Command Line Interface (CLI)

Once installed, you can use the `pdify` CLI directly:
```bash
pdify --input path/to/document.pdf
```
Run `pdify --help` to view all available CLI options.

### Python API

You can also import and use `pdify` programmatically in your own Python projects:

```python
import pdfplumber
from pdify import extract_text_and_words_with_layout, get_cohesive_figure_boxes

# Open a PDF file
with pdfplumber.open("document.pdf") as pdf:
    # Process the first page
    page = pdf.pages[0]
    
    # 1. Get cohesive figure boxes (automatically merging nested crops)
    figure_boxes = get_cohesive_figure_boxes(page)
    
    # 2. Extract text layout while ignoring headers/footers and text within figure boxes
    text_content, words = extract_text_and_words_with_layout(
        page,
        remove_headers_footers=True,
        exclude_figure_text=True,
        figure_boxes=figure_boxes
    )
    
    print(text_content)
```

## Output Structure

Outputs are saved to `extracted_content/<pdf_name>/` by default:
- **`text/`**: Clean page text (`page_N_text.txt`) and word coordinate metadata (`page_N_text_metadata.json`).
- **`images/`**: High-resolution cohesive figure crops (`page_N_figure_M.png`) and bounding box metadata (`page_N_figure_M_metadata.json`).

## License
MIT License. See [LICENSE](LICENSE) for details.

