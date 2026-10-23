/**
 * The IJ harness conversation reads like a document (v1.373.0, the user's
 * report: a Build session's reply ran edge to edge in small, tight text
 * beside Claude's own app, whose spacing and font they preferred).
 *
 * The harness used to borrow the Build PANE chat's look (`terminal/
 * paneChatLook.ts`), sized for a pane that can be 280 px wide; at full width
 * a reply's lines ran ~1,240 px. Here: ONE centred column for the transcript
 * and the message box (the chat page's 760 px), 15 px text with more room
 * between lines, paragraphs and list items. The pane chat keeps its compact
 * look — these strings are the harness's own.
 *
 * Theme tokens only (`zinc-*`, `accent`, `white` overlays), so the dark
 * Marks, Daylight and the user's own palettes all read right. Whole pixels.
 */

/** The column under the transcript (the questions, the queue, the box):
 *  the chat page's 760 px reading width plus the wrapper's own 4 px padding
 *  either side, so the box lines up with the replies above it. */
export const HARNESS_COLUMN = "mx-auto w-full max-w-[768px]";

/** On the transcript: every row sits in the reading column. */
export const HARNESS_TRANSCRIPT_ROWS = "[&>*]:mx-auto [&>*]:w-full [&>*]:max-w-[760px]";

/** A reply: prose on the page at reading size. The Markdown renderer's own
 *  spacing is compact (6 px between paragraphs, 4 px between list items);
 *  these rules out-rank it inside a reply only. Tables keep the calm
 *  hairline look the chat page uses. */
export const HARNESS_REPLY_PROSE =
  "min-w-0 text-[15px] leading-[1.7] text-zinc-200 " +
  "[&_p]:my-3 [&_ul]:my-3 [&_ol]:my-3 [&_ul]:space-y-1.5 [&_ol]:space-y-1.5 " +
  "[&_p]:leading-[1.7] [&_li]:leading-[1.7] [&_h1]:mt-6 [&_h2]:mt-5 [&_h3]:mt-4 " +
  "[&_pre]:my-3 [&_table]:text-[13px] " +
  "[&_th]:border-x-0 [&_th]:border-t-0 [&_th]:border-white/[0.16] [&_th]:bg-transparent [&_th]:py-1.5 [&_th]:pl-0 [&_th]:pr-3 [&_th]:font-medium [&_th]:text-zinc-400 " +
  "[&_td]:border-x-0 [&_td]:border-t-0 [&_td]:border-white/[0.08] [&_td]:py-1.5 [&_td]:pl-0 [&_td]:pr-3";

/** Your own message: the soft tinted bubble, at the reply's size. */
export const HARNESS_USER_BUBBLE =
  "min-w-0 max-w-[85%] whitespace-pre-wrap break-words rounded-[20px] bg-accent/[0.1] px-4 py-2.5 text-[15px] leading-[1.6] text-zinc-100 [overflow-wrap:anywhere]";

/** The message box inside the composer card, at the reply's size. */
export const HARNESS_COMPOSER_BOX =
  "min-h-[52px] w-full resize-none bg-transparent px-4 pb-1 pt-3 text-[15px] text-zinc-100 placeholder:text-zinc-500 focus:outline-none disabled:opacity-50";
