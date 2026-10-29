// Chat attachments: the same rules as server/app/uploads.py (contract §10), checked before uploading
// so a 30 MB file is refused at once rather than after a long upload. The server stays the authority.

const MB = 1024 * 1024;

const LIMITS: Record<string, number> = {
  'image/png': 5 * MB, 'image/jpeg': 5 * MB, 'image/webp': 5 * MB, 'image/gif': 5 * MB,
  'application/pdf': 20 * MB,
  'text/plain': MB, 'text/markdown': MB, 'text/csv': MB,
};
const TEXT_EXT: Record<string, string> = { txt: 'text/plain', md: 'text/markdown', csv: 'text/csv' };

/** What the file picker offers. */
export const ACCEPT = 'image/png,image/jpeg,image/webp,image/gif,application/pdf,.txt,.md,.csv';

export function fileType(name: string, type: string): string {
  const t = type.toLowerCase() === 'image/jpg' ? 'image/jpeg' : type.toLowerCase();
  if (t in LIMITS) return t;
  const ext = name.includes('.') ? name.split('.').pop()!.toLowerCase() : '';
  return TEXT_EXT[ext] ?? t;
}

/** A sentence saying why this file can't be attached, or null if it can. */
export function refuse(name: string, type: string, size: number): string | null {
  const t = fileType(name, type);
  const lower = name.toLowerCase();
  if (!(t in LIMITS)) {
    if (/\.(docx?|pptx?|xlsx?)$/.test(lower)) return `${name}: Office files can't be read by the AI yet. Save it as PDF and attach that.`;
    if (/\.hei[cf]$/.test(lower)) return `${name}: iPhone HEIC photos can't be read. Export it as JPEG and attach that.`;
    return `${name}: only pictures (PNG, JPEG, WebP, GIF), PDFs and text files can be attached.`;
  }
  if (size <= 0) return `${name} is empty.`;
  if (size > LIMITS[t]) return `${name} is ${(size / MB).toFixed(1)} MB; the limit for this kind of file is ${LIMITS[t] / MB} MB.`;
  return null;
}

export const isImage = (type: string) => type.startsWith('image/');

export function sizeText(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < MB) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / MB).toFixed(1)} MB`;
}

/** The file as base64 (a data: URL), which is how the JSON API carries it. */
export function readAsDataUrl(file: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => resolve(String(r.result));
    r.onerror = () => reject(r.error ?? new Error('Could not read the file'));
    r.readAsDataURL(file);
  });
}
