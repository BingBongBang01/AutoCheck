"""KnowledgeApiMixin — 프로젝트 폴더 밑 knowledge/*.md 를 탐색·조회하는 간단한 문서 탐색기."""
import os


class KnowledgeApiMixin:
    def _knowledge_dir(self, paths):
        base = os.path.dirname(paths["target_state"])
        kdir = os.path.join(base, "knowledge")
        os.makedirs(kdir, exist_ok=True)
        return kdir

    def list_knowledge_docs(self):
        try:
            paths = self._paths()
        except RuntimeError:
            return []
        kdir = self._knowledge_dir(paths)
        docs = []
        for fname in sorted(os.listdir(kdir)):
            if fname.endswith(".md"):
                full = os.path.join(kdir, fname)
                docs.append({"name": fname, "size": os.path.getsize(full)})
        return docs

    def get_knowledge_doc(self, name):
        try:
            paths = self._paths()
        except RuntimeError:
            return ""
        kdir = self._knowledge_dir(paths)
        full = os.path.join(kdir, name)
        if not os.path.exists(full) or not os.path.abspath(full).startswith(os.path.abspath(kdir)):
            return ""
        with open(full, encoding="utf-8") as f:
            return f.read()

    def save_knowledge_doc(self, name, content):
        try:
            paths = self._paths()
        except RuntimeError:
            return False
        kdir = self._knowledge_dir(paths)
        if not name.endswith(".md"):
            name += ".md"
        full = os.path.join(kdir, name)
        with open(full, "w", encoding="utf-8") as f:
            f.write(content)
        return True

    def delete_knowledge_doc(self, name):
        try:
            paths = self._paths()
        except RuntimeError:
            return False
        kdir = self._knowledge_dir(paths)
        full = os.path.join(kdir, name)
        if os.path.exists(full) and os.path.abspath(full).startswith(os.path.abspath(kdir)):
            os.remove(full)
            return True
        return False
