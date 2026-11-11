import uuid

from django.db import models


class Workspace(models.Model):
    """One visitor's private set of documents. Tied to their session, deleted after the retention window."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return str(self.id)


class Document(models.Model):
    # workspace is null only for the built-in sample document, which every visitor can query.
    workspace = models.ForeignKey(Workspace, null=True, blank=True, on_delete=models.CASCADE, related_name="documents")
    name = models.CharField(max_length=200)
    pages = models.PositiveIntegerField(default=1)
    chars = models.PositiveIntegerField(default=0)
    is_sample = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-is_sample", "created_at"]

    def __str__(self) -> str:
        return self.name


class Chunk(models.Model):
    """A passage of a document. Only extracted text is stored; the uploaded file itself is discarded."""

    document = models.ForeignKey(Document, on_delete=models.CASCADE, related_name="chunks")
    page = models.PositiveIntegerField()
    ordinal = models.PositiveIntegerField()
    text = models.TextField()

    class Meta:
        ordering = ["document_id", "ordinal"]
        indexes = [models.Index(fields=["document", "ordinal"])]

    def __str__(self) -> str:
        return f"{self.document_id}:{self.ordinal}"
