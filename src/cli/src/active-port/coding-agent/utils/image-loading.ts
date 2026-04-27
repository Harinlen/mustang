// -nocheck
export const MAX_IMAGE_INPUT_BYTES = 20 * 1024 * 1024;
export const SUPPORTED_INPUT_IMAGE_MIME_TYPES = new Set(["image/png", "image/jpeg", "image/webp"]);
export class ImageInputTooLargeError extends Error {}
export async function ensureSupportedImageInput<T>(image: T): Promise<T> {
	return image;
}
