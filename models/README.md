# Constellation ONNX models

The consumer embedding engine auto-loads an ONNX image encoder from this directory when present.

Expected default file:

```text
models/mobileclip-s1-vision.onnx
models/preprocessor_config.json
```

Download the default MobileCLIP-S1 model from Hugging Face with:

```bash
pnpm studio:download-onnx
```

Default source:

```text
Xenova/mobileclip_s1
onnx/vision_model.onnx
preprocessor_config.json
```

This is a MobileCLIP-S1 ONNX image encoder converted for Transformers.js. Runtime needs ONNX Runtime, not PyTorch. Embeddings stay local. Downloader metadata records source URLs, license URL, revision, and SHA-256 checksums for release review.

The legacy OpenAI CLIP ViT-B/32 ONNX model remains available:

```bash
pnpm studio:download-onnx -- --model clip-vit-base-patch32
```

Legacy source:

```text
Xenova/clip-vit-base-patch32
onnx/vision_model.onnx
```
