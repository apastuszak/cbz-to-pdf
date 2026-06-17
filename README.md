# cbz-to-pdf

Convert CBZ and CBR comic archive files to PDF. Metadata from `ComicInfo.xml` is automatically mapped to PDF metadata fields. Images are embedded without re-encoding, preserving the original compression.

## Features

- Converts `.cbz` (ZIP-based) and `.cbr` (RAR-based) archives
- Embeds JPEG and PNG images directly — no re-encoding, no quality loss
- Converts unsupported formats (WebP, etc.) to PNG as a fallback
- Reads `ComicInfo.xml` and populates standard PDF metadata fields
- Natural sort order for image pages (page 10 comes after page 9, not page 1)
- Guards against malicious archives (zip bombs, oversized members)

## Requirements

### Python packages

```
pip install -r requirements.txt
```

| Package | Purpose |
|---|---|
| `img2pdf` | Losslessly embeds images into PDF |
| `Pillow` | Converts unsupported image formats to PNG |
| `pypdf` | Writes PDF metadata |
| `rarfile` | Reads CBR (RAR) archives — optional, only needed for `.cbr` files |

### System dependencies

**CBR support** requires a RAR extraction tool on your `PATH`. Install one of:

- **macOS**: `brew install unar` or `brew install rar`
- **Debian/Ubuntu**: `apt install unrar` or `apt install unar`
- **Windows**: install [WinRAR](https://www.rarlab.com/) or [7-Zip](https://www.7-zip.org/)

CBZ files work without any system dependency.

## Installation

```bash
git clone https://github.com/apastuszak/cbz-to-pdf.git
cd cbz-to-pdf
pip install -r requirements.txt
```

## Usage

```
python cbz_to_pdf.py <input> <output>
```

### Arguments

| Argument | Description |
|---|---|
| `input` | Path to a `.cbz` or `.cbr` file |
| `output` | Path for the output `.pdf` file |

### Examples

```bash
# Convert a CBZ file
python cbz_to_pdf.py "The Sandman 001.cbz" "The Sandman 001.pdf"

# Convert a CBR file
python cbz_to_pdf.py "Watchmen 01.cbr" "Watchmen 01.pdf"

# Use absolute paths
python cbz_to_pdf.py /comics/input.cbz /output/result.pdf
```

## ComicInfo.xml metadata mapping

If the archive contains a `ComicInfo.xml` file (standard in many comic management tools), its fields are mapped to PDF metadata:

| ComicInfo.xml field(s) | PDF metadata field |
|---|---|
| `Series`, `Number`, `Title` | `/Title` — e.g. `Amazing Spider-Man #1 The Beginning` |
| `Writer`, `Penciller`, `Inker`, `Colorist`, `CoverArtist`, `Letterer`, `Editor` | `/Author` — comma-separated |
| `Summary` | `/Subject` |
| `Publisher`, `Genre`, `LanguageISO`, `AgeRating` | `/Keywords` — comma-separated |
| `Year`, `Month`, `Day` | `/CreationDate` |

Fields absent from the XML are silently skipped. If no `ComicInfo.xml` is present, the PDF is created with no metadata.

### Example ComicInfo.xml

```xml
<?xml version="1.0"?>
<ComicInfo>
  <Title>The Beginning</Title>
  <Series>Amazing Spider-Man</Series>
  <Number>1</Number>
  <Summary>Peter Parker's first outing as Spider-Man.</Summary>
  <Writer>Stan Lee</Writer>
  <Penciller>Steve Ditko</Penciller>
  <Publisher>Marvel Comics</Publisher>
  <Genre>Superhero</Genre>
  <Year>1963</Year>
  <Month>3</Month>
</ComicInfo>
```

## Image handling

Images are processed in natural filename order (`001.jpg`, `002.jpg`, ..., `010.jpg`).

| Format | Treatment |
|---|---|
| JPEG | Embedded as-is — no re-encoding |
| PNG | Embedded as-is — no re-encoding |
| TIFF, GIF, BMP | Embedded directly via `img2pdf` |
| WebP, and others | Converted to PNG before embedding |

## Limits

To protect against malicious or corrupt archives:

| Limit | Value |
|---|---|
| Maximum members in archive | 10,000 |
| Maximum uncompressed size per file | 200 MB |
| Maximum total uncompressed size | 4 GB |

These limits can be adjusted by editing the constants at the top of `cbz_to_pdf.py`.

## Troubleshooting

**`rarfile` package not installed**
```
rarfile package required for CBR files. Install with: pip install rarfile
```
Install `rarfile` and a system RAR tool (see Requirements above).

**`unrar` not found on PATH**
```
rarfile.RarCannotExec: ...
```
Install `unrar` or `unar` and ensure it is on your system `PATH`.

**No image files found**
```
No image files found in archive
```
The archive contains no files with recognised image extensions (`.jpg`, `.jpeg`, `.png`, `.gif`, `.webp`, `.bmp`, `.tiff`, `.tif`). Inspect the archive contents with `unzip -l file.cbz`.

**Archive member too large / total size exceeded**
The archive exceeds the built-in safety limits. If the archive is legitimate, increase `MAX_MEMBER_SIZE` or `MAX_TOTAL_SIZE` in `cbz_to_pdf.py`.

**Decompression bomb detected**
```
PIL.Image.DecompressionBombError: ...
```
An image in the archive has an extremely large pixel count (> 178 megapixels). This is a safety limit in Pillow. Raise `PIL.Image.MAX_IMAGE_PIXELS` at the top of the script only if you trust the source file.

## License

MIT
