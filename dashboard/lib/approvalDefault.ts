/**
 * v1.353.0: ONE default for "Approvals in chat".
 *
 * Settings › Permissions has a row, `device.approval_mode` ("Approvals in
 * chat"), that chat's own settings tools can change too ("set approvals to
 * Don't ask"). Until v1.353.0 nothing read it: the composer's permission chip
 * kept its default in localStorage only, so the row was a second dial that
 * did nothing. Now they are one dial:
 *
 *  - the chat page reads the row ONCE when it opens; a value the user chose
 *    there (or asked chat to set) becomes the default a new chat starts with
 *    (mirrored into the chip's localStorage default). A thread's own saved
 *    posture and a press on the chip still win for that conversation;
 *  - a press on the chip writes the same row back, so Settings shows what
 *    the chip shows.
 *
 * A row never chosen ("not set") changes nothing: the chip keeps the
 * default it had. Kept out of lib/api.ts (most page tests mock that module
 * wholesale); every call here is best effort and never throws.
 */

import { get, put } from "@/lib/api";
import { getDeviceId } from "@/lib/device";
import { PERMISSION_MODES, type PermissionMode } from "@/lib/permissionLevels";

/** The Settings row (settings/schema.py `device.approval_mode`). */
export const DEVICE_APPROVAL_KEY = "device.approval_mode";

/** The row's value out of a GET/PUT /settings/device answer, or null when
 *  it is not set (or not one of the three levels). */
export function deviceApprovalFrom(raw: unknown): PermissionMode | null {
  if (!raw || typeof raw !== "object") return null;
  const values = (raw as { values?: unknown }).values;
  if (!values || typeof values !== "object") return null;
  const v = (values as Record<string, unknown>)[DEVICE_APPROVAL_KEY];
  return typeof v === "string" && (PERMISSION_MODES as readonly string[]).includes(v)
    ? (v as PermissionMode)
    : null;
}

/** This device's "Approvals in chat", or null (not set, an older daemon, a
 *  failed read). */
export async function readDeviceApproval(): Promise<PermissionMode | null> {
  try {
    const raw = await get<unknown>(
      `/settings/device?device_id=${encodeURIComponent(getDeviceId())}`,
    );
    return deviceApprovalFrom(raw);
  } catch {
    return null;
  }
}

/** Save the chip's pick as this device's "Approvals in chat". True when the
 *  daemon took it. */
export async function writeDeviceApproval(mode: PermissionMode): Promise<boolean> {
  try {
    await put("/settings/device", {
      device_id: getDeviceId(),
      values: { [DEVICE_APPROVAL_KEY]: mode },
    });
    return true;
  } catch {
    return false;
  }
}
