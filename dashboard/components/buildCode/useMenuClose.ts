/**
 * v1.441.0 (integration I6): how a small ⋯ menu in the Code view closes —
 * the rule the Iron Jarvis tools chip (JarvisTools) and the Session tab's
 * menus (SessionControls) already follow, shared by the session row's ⋯
 * (BuildCodeView) and a message's ⋯ (CodeConversation):
 *
 *  - a press OUTSIDE the menu's box (its button and its list) closes it;
 *  - Escape closes it and puts the focus back on its button, so the next
 *    Escape (which stops a running reply from the box) is never eaten by a
 *    menu nobody sees.
 *
 * `MENU_BUTTON_HOVER_FADE` is the button's "only on hover" classes: drop them
 * while the menu is open, or on a mouse device the button fades away (the
 * pointer moved into the list) under a menu that is still open.
 */
import { useEffect, type RefObject } from "react";

export const MENU_BUTTON_HOVER_FADE = "[@media(hover:hover)]:opacity-0";

export function useMenuClose(
  open: boolean,
  box: RefObject<HTMLElement | null>,
  close: () => void,
  button?: RefObject<HTMLElement | null>,
): void {
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      const el = box.current;
      if (el && !el.contains(e.target as Node)) close();
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      e.preventDefault();
      close();
      button?.current?.focus();
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open, box, close, button]);
}
