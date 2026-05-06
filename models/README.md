# Constellation ONNX models

The consumer embedding engine auto-loads an ONNX image encoder from this directory when present.

Expected default file:

```text
models/clip-image-encoder.onnx
```

Download it from Hugging Face with:

```bash
pnpm studio:download-onnx
```

Default source:

```text
Xenova/clip-vit-base-patch32
onnx/vision_model.onnx
```

This is an ONNX OpenAI CLIP ViT-B/32 image encoder. Runtime needs ONNX Runtime, not PyTorch.
