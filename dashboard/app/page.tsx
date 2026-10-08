import ChatPage from "./chat/page";

/**
 * HOME IS A NEW CHAT (calm UI redesign S8, AUDIT §4.1 / Q1, brief T1).
 *
 * `/` renders the chat surface itself — the composer on the page and focused,
 * the model chosen inside it, recent chats and projects in the sidebar. The
 * old Overview's operational content is Everything › Status
 * (components/overview/StatusOverview.tsx). `/chat` is the same surface.
 */
export default function HomePage() {
  return <ChatPage />;
}
