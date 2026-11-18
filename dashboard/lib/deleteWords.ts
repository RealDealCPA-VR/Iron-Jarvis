/** v1.535.0 (fourth review C-DT-2, owner decision D5): the armed Delete's
 *  words, for the chat's More menu and the chat list's row menu. Deleting a
 *  chat stops the reply it is writing (the daemon's `delete_thread`), so the
 *  armed press says so when the chat has one running. */
export const DELETE_ARMED_WORDS = "Delete for good? Press again";
export const DELETE_STOPS_REPLY_WORDS = "Deleting it also stops the reply it is writing.";

export function deleteArmedWords(running: boolean): string {
  return running ? `${DELETE_ARMED_WORDS} ${DELETE_STOPS_REPLY_WORDS}` : DELETE_ARMED_WORDS;
}
