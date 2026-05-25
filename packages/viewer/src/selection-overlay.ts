import {
  BufferGeometry,
  CanvasTexture,
  DoubleSide,
  Float32BufferAttribute,
  Group,
  Line,
  LineBasicMaterial,
  Mesh,
  MeshBasicMaterial,
  PerspectiveCamera,
  PlaneGeometry,
  SRGBColorSpace,
  Vector3,
} from 'three';

import type { PositionedImage } from './types';

const panelCanvasWidth = 1024;
const panelCanvasHeight = 640;
const panelPadding = 40;
const panelFooterHeight = 78;
const selectionStrokeColor = '#ffffff';
const panelStrokeWidth = 2;
const bodyFontSize = 24;
const bodyLineHeight = 30;
const footerFontSize = 21;

export class SelectionInfoOverlay {
  readonly object = new Group();

  private readonly cameraForward = new Vector3();
  private selectedImage: PositionedImage | null = null;
  private border: Line<BufferGeometry, LineBasicMaterial> | null = null;
  private panel: Mesh<PlaneGeometry, MeshBasicMaterial> | null = null;
  private panelTexture: CanvasTexture | null = null;

  constructor() {
    this.object.visible = false;
  }

  setImage(image: PositionedImage | null): void {
    if (this.selectedImage?.id === image?.id) return;
    this.selectedImage = image;
    this.rebuild();
  }

  update(camera: PerspectiveCamera, position: Vector3, cardWidth: number, cardHeight: number): void {
    if (!this.selectedImage || !this.border || !this.panel) return;

    camera.updateMatrixWorld(true);
    camera.getWorldDirection(this.cameraForward).normalize();

    this.object.visible = true;
    this.object.position.copy(position).addScaledVector(this.cameraForward, -0.12);
    this.object.quaternion.copy(camera.quaternion);

    const borderScale = 1.08;
    this.border.position.set(0, 0, 0.04);
    this.border.scale.set(cardWidth * borderScale, cardHeight * borderScale, 1);

    const panelHeight = Math.max(cardHeight * 1.05, 7.5);
    const panelWidth = panelHeight * 1.03;
    const gap = Math.max(cardHeight * 0.22, 1.8);
    this.panel.position.set((cardWidth / 2) + gap + (panelWidth / 2), 0, 0.05);
    this.panel.scale.set(panelWidth, panelHeight, 1);
  }

  dispose(): void {
    this.disposeObjects();
  }

  private rebuild(): void {
    this.disposeObjects();
    if (!this.selectedImage) {
      this.object.visible = false;
      return;
    }

    this.border = createBorder();
    this.panelTexture = createInfoPanelTexture(this.selectedImage);
    this.panel = new Mesh(
      new PlaneGeometry(1, 1),
      new MeshBasicMaterial({
        map: this.panelTexture,
        transparent: true,
        depthTest: false,
        depthWrite: false,
        side: DoubleSide,
      }),
    );
    this.panel.renderOrder = 41;
    this.object.add(this.border);
    this.object.add(this.panel);
    this.object.visible = true;
  }

  private disposeObjects(): void {
    if (this.border) {
      this.object.remove(this.border);
      this.border.geometry.dispose();
      this.border.material.dispose();
      this.border = null;
    }
    if (this.panel) {
      this.object.remove(this.panel);
      this.panel.geometry.dispose();
      this.panel.material.dispose();
      this.panel = null;
    }
    if (this.panelTexture) {
      this.panelTexture.dispose();
      this.panelTexture = null;
    }
  }
}

function createBorder(): Line<BufferGeometry, LineBasicMaterial> {
  const geometry = new BufferGeometry();
  geometry.setAttribute(
    'position',
    new Float32BufferAttribute([
      -0.5, -0.5, 0,
      0.5, -0.5, 0,
      0.5, 0.5, 0,
      -0.5, 0.5, 0,
      -0.5, -0.5, 0,
    ], 3),
  );
  const material = new LineBasicMaterial({
    color: 0xffffff,
    depthTest: false,
    depthWrite: false,
    transparent: true,
    opacity: 1,
  });
  const line = new Line(geometry, material);
  line.renderOrder = 40;
  return line;
}

function createInfoPanelTexture(image: PositionedImage): CanvasTexture {
  const canvas = document.createElement('canvas');
  canvas.width = panelCanvasWidth;
  canvas.height = panelCanvasHeight;
  const context = canvas.getContext('2d');
  if (!context) throw new Error('Could not create selection info canvas context');

  context.clearRect(0, 0, panelCanvasWidth, panelCanvasHeight);
  context.fillStyle = 'rgba(2, 2, 4, 0.88)';
  context.fillRect(0, 0, panelCanvasWidth, panelCanvasHeight);
  context.strokeStyle = selectionStrokeColor;
  context.lineWidth = panelStrokeWidth;
  const strokeInset = panelStrokeWidth / 2;
  context.strokeRect(strokeInset, strokeInset, panelCanvasWidth - panelStrokeWidth, panelCanvasHeight - panelStrokeWidth);

  context.fillStyle = selectionStrokeColor;
  context.textBaseline = 'top';
  context.font = `${bodyFontSize}px ui-monospace, SFMono-Regular, Menlo, Consolas, monospace`;

  const body = ocrTextForImage(image);
  const maxBodyLines = Math.floor((panelCanvasHeight - panelFooterHeight - (panelPadding * 1.5)) / bodyLineHeight);
  const bodyLines = wrapText(context, body, panelCanvasWidth - (panelPadding * 2), maxBodyLines);
  let y = panelPadding;
  for (const line of bodyLines) {
    context.fillText(line, panelPadding, y);
    y += bodyLineHeight;
  }

  const footer = pageIssueLabel(image);
  if (footer) {
    context.strokeStyle = 'rgba(255, 255, 255, 0.45)';
    context.lineWidth = 2;
    context.beginPath();
    context.moveTo(panelPadding, panelCanvasHeight - panelFooterHeight);
    context.lineTo(panelCanvasWidth - panelPadding, panelCanvasHeight - panelFooterHeight);
    context.stroke();

    context.font = `${footerFontSize}px ui-monospace, SFMono-Regular, Menlo, Consolas, monospace`;
    context.fillStyle = selectionStrokeColor;
    context.fillText(truncateToWidth(context, footer, panelCanvasWidth - (panelPadding * 2)), panelPadding, panelCanvasHeight - 58);
  }

  const texture = new CanvasTexture(canvas);
  texture.colorSpace = SRGBColorSpace;
  texture.needsUpdate = true;
  return texture;
}

function ocrTextForImage(image: PositionedImage): string {
  const metadata = image.metadata ?? {};
  for (const key of ['ocrText', 'ocr', 'ocrResult', 'ocr_result', 'transcription', 'text']) {
    const value = metadata[key];
    if (typeof value === 'string' && value.trim().length > 0) return value.trim();
  }
  return 'No OCR transcription available for this page.';
}

function pageIssueLabel(image: PositionedImage): string {
  const metadata = image.metadata ?? {};
  const pageNumber = stringMetadata(metadata, 'pageNumber');
  const issue = stringMetadata(metadata, 'issueFilename')
    ?? stringMetadata(metadata, 'publicationDate')
    ?? stringMetadata(metadata, 'issueId');
  const parts: string[] = [];
  if (pageNumber) parts.push(`Page ${pageNumber}`);
  if (issue) parts.push(`Issue ${issue}`);
  return parts.join(' · ');
}

function stringMetadata(metadata: Record<string, unknown>, key: string): string | null {
  const value = metadata[key];
  if (typeof value === 'string' && value.trim().length > 0) return value.trim();
  if (typeof value === 'number') return String(value);
  return null;
}

function wrapText(context: CanvasRenderingContext2D, text: string, maxWidth: number, maxLines: number): string[] {
  const words = text.replace(/\s+/g, ' ').trim().split(' ').filter(Boolean);
  const lines: string[] = [];
  let line = '';

  for (const word of words) {
    const next = line ? `${line} ${word}` : word;
    if (context.measureText(next).width <= maxWidth) {
      line = next;
      continue;
    }
    if (line) lines.push(line);
    line = word;
    if (lines.length >= maxLines) break;
  }
  if (line && lines.length < maxLines) lines.push(line);

  if (lines.length === maxLines && words.length > 0) {
    lines[maxLines - 1] = truncateToWidth(context, `${lines[maxLines - 1]}…`, maxWidth);
  }
  return lines.length > 0 ? lines : [''];
}

function truncateToWidth(context: CanvasRenderingContext2D, text: string, maxWidth: number): string {
  if (context.measureText(text).width <= maxWidth) return text;
  let truncated = text;
  while (truncated.length > 1 && context.measureText(`${truncated}…`).width > maxWidth) {
    truncated = truncated.slice(0, -1);
  }
  return `${truncated}…`;
}
