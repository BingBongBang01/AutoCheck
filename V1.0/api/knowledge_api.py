"""KnowledgeApiMixin — 프로젝트 폴더 밑 knowledge/*.md 를 탐색·조회하는 간단한 문서 탐색기."""
import os

from core.paths import safe_child


class KnowledgeApiMixin:
    def _knowledge_dir(self, paths):
        base = os.path.dirname(paths["target_state"])
        kdir = os.path.join(base, "knowledge")
        os.makedirs(kdir, exist_ok=True)
        return kdir

    @staticmethod
    def _knowledge_file(kdir, name):
        """knowledge 폴더 '바로 아래' 파일 하나. 이름은 화면에서 입력·선택한 값이라 '..\\x', 'C:\\x' 같은
        이름으로 폴더 밖을 읽고·쓰고·지우지 못하게 한다(예전 startswith 검사는 'knowledge2' 같은
        옆 폴더를 통과시켰고, 저장에는 검사가 아예 없었다). 허용되지 않으면 None."""
        try:
            return str(safe_child(kdir, name))
        except ValueError:
            return None

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
        full = self._knowledge_file(self._knowledge_dir(paths), name)
        if not full or not os.path.isfile(full):
            return ""
        with open(full, encoding="utf-8") as f:
            return f.read()

    def save_knowledge_doc(self, name, content):
        try:
            paths = self._paths()
        except RuntimeError:
            return False
        name = str(name or "")
        if not name.endswith(".md"):
            name += ".md"
        full = self._knowledge_file(self._knowledge_dir(paths), name)
        if not full:
            return False
        with open(full, "w", encoding="utf-8") as f:
            f.write(content)
        return True

    def delete_knowledge_doc(self, name):
        try:
            paths = self._paths()
        except RuntimeError:
            return False
        full = self._knowledge_file(self._knowledge_dir(paths), name)
        if full and os.path.isfile(full):
            os.remove(full)
            return True
        return False
