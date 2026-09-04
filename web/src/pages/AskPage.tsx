import { useAskChat } from "@/hooks/useAskChat";
import { ChatThread } from "@/components/ask/ChatThread";

export function AskPage() {
  const chat = useAskChat();

  return (
    <div className="mx-auto h-full max-w-3xl">
      <ChatThread
        messages={chat.messages}
        isStreaming={chat.isStreaming}
        error={chat.error}
        onSend={chat.send}
        onStop={chat.stop}
      />
    </div>
  );
}
