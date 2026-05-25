# PDIfy

PDIfy is a Python utility to extract clean text layout structures and cohesive figures from PDF documents (like research papers or reports), preventing fragmented crops or nested image duplication.

## Features
- **Cohesive Figure Extraction**: Groups overlapping/nested image elements into single cohesive crops.
- **Layout Reconstruction**: Reconstructs paragraphs and column spans in correct visual reading order.
- **Hyphenation & Ligatures**: Automatically resolves word hyphens and common ligatures.
- **Parallel Processing**: Uses multi-processing to extract content concurrently.

## Installation
```bash
pip install -r requirements.txt
```
*Note: Depending on your system and PDF files, you may also need `ghostscript` if rendering certain vector formats.*

## Usage
```bash
python convert.py --input path/to/document.pdf
```
Run `python convert.py --help` to view all available CLI options.

## Output Structure
Outputs are saved to `extracted_content/<pdf_name>/`:
- **`text/`**: Clean page text (`page_N_text.txt`) and word coordinate metadata (`page_N_text_metadata.json`).
- **`images/`**: High-resolution cohesive figure crops (`page_N_figure_M.png`) and bounding box metadata (`page_N_figure_M_metadata.json`).

## License
MIT License. See [LICENSE](LICENSE) for details.

