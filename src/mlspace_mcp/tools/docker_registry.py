"""``mlspace_docker_registry`` — manage the MLSpace Docker registry.

Pure declarative data following the EXEMPLAR ``inference.py``. The registry has
multiple resource types (registry, repositories, images, tags, credentials), so
action names suffix the noun. All request mechanics live in the core.
"""

from __future__ import annotations

from ..registry import DomainTool, Op, Param

# action -> Op. The (method, path) pairs are the single source of truth for the
# drift test; paths are copied verbatim from the spec, placeholders included.
ACTIONS: dict[str, Op] = {
    "current_registry": Op(
        "GET", "/public/v2/docker_registry/v2/registries/current",
        help="Get the current Docker registry for the workspace.",
    ),
    "list_repos": Op(
        "GET", "/public/v2/docker_registry/v2/repositories/",
        # page/page_size are a working proband lever here. NOTE we deliberately do NOT
        # paginate list_images/list_tags: there pagination splits ONE image across
        # pages with partial tags (08#4), so surfacing it would amplify that bug.
        query_params={"page": "page", "page_size": "page_size"},
        # registry_repos kind (not plain "list") elides the heavy recent_image.
        # extra_attrs (~39% of the response) with a pointer to get_image.
        kind="registry_repos",
        help="List repositories in the registry.",
    ),
    "get_repo": Op(
        "GET", "/public/v2/docker_registry/v2/repositories/{repository_id}",
        path_params={"repository_id": "repository_id"},
        required=("repository_id",),
        help="Get one repository.",
    ),
    "update_repo": Op(
        "PUT", "/public/v2/docker_registry/v2/repositories/{repository_id}",
        path_params={"repository_id": "repository_id"},
        body_field="body",
        required=("repository_id", "body"),
        write=True,
        help="Update a repository; `body` = UpdateRepository: "
             "{description, labels, labels_to_delete}. MCP verifies label additions/removals "
             "with get_repo. Verification failure means the write may already have happened; "
             "do not repeat it automatically.",
    ),
    "delete_repos": Op(
        "DELETE", "/public/v2/docker_registry/v2/repositories/",
        body_field="body",
        required=("body",),
        write=True,
        confirm=True,
        help="Delete repositories; `body` = DeleteRepositories.",
    ),
    "repo_fav": Op(
        "POST", "/public/v2/docker_registry/v2/repositories/{repository_id}/fav",
        path_params={"repository_id": "repository_id"},
        body_field="body",
        required=("repository_id", "body"),
        write=True,
        help="Mark/unmark a repository as favorite; `body` = FavoriteInput.",
    ),
    "list_images": Op(
        "GET", "/public/v2/docker_registry/v2/images/",
        query_params={"repository_id": "repository_id"},
        required=("repository_id",),
        kind="list",
        help="List images in a repository (requires repository_id).",
    ),
    "get_image": Op(
        "GET", "/public/v2/docker_registry/v2/images/{image_id}",
        path_params={"image_id": "image_id"},
        required=("image_id",),
        help="Get one image.",
    ),
    "delete_images": Op(
        "DELETE", "/public/v2/docker_registry/v2/images/",
        body_field="body",
        required=("body",),
        write=True,
        confirm=True,
        help="Delete images; `body` = DeleteImages.",
    ),
    "list_tags": Op(
        "GET", "/public/v2/docker_registry/v2/tags/",
        query_params={"image_id": "image_id"},
        required=("image_id",),
        kind="list",
        help="List tags for an image (requires image_id).",
    ),
    "create_tag": Op(
        "POST", "/public/v2/docker_registry/v2/tags/",
        body_field="body",
        required=("body",),
        write=True,
        help="Create a tag; `body` = CreateTag.",
    ),
    "update_tag": Op(
        "PUT", "/public/v2/docker_registry/v2/tags/{tag_id}",
        path_params={"tag_id": "tag_id"},
        body_field="body",
        required=("tag_id", "body"),
        write=True,
        help="Update a tag; `body` = UpdateTag.",
    ),
    "delete_tags": Op(
        "DELETE", "/public/v2/docker_registry/v2/tags/",
        body_field="body",
        required=("body",),
        write=True,
        confirm=True,
        help="Delete tags; `body` = DeleteTags.",
    ),
    "generate_password": Op(
        "GET", "/public/v2/docker_registry/v1/users/generate_password",
        write=True,
        # irreversible credential rotation: the previous password immediately stops
        # working, so a single mistaken call would lock out anything using it. Gate it
        # behind confirm=true like the other destructive ops.
        confirm=True,
        reveal_result_keys=("password",),
        help="Rotate and return the registry password for your personal account "
             "(not service accounts). The previous password stops working. "
             "Disabled by default: the operator must explicitly enable "
             "MLSPACE_ALLOW_REGISTRY_PASSWORD_ROTATION=true in server configuration after "
             "approving rotation. Never enable it yourself to get past a refusal. "
             "Requires confirm=true. For an authorized docker login task, also set "
             "reveal_secret=true in this same call to receive the new password; "
             "otherwise it is masked. Reuse existing login/credentials first. Obtain "
             "authorization for rotation and disclosure to the agent client; never echo "
             "the password in ordinary replies. Do not rotate again just to reveal a masked result.",
    ),
}

PARAMS: list[Param] = [
    Param("repository_id", str, "Repository ID (UUID)."),
    Param("image_id", str, "Image ID (UUID); also the query filter for list_images/list_tags."),
    Param("tag_id", str, "Tag ID (UUID)."),
    Param(
        "page",
        int,
        "Page number (list_repos only, 1-based). Track the pages checked; "
        "a short/empty page alone does not establish completeness.",
    ),
    Param("page_size", int, "Page size (list_repos)."),
    Param(
        "body",
        dict,
        "JSON body. update_repo=UpdateRepository; delete_repos=DeleteRepositories "
        "{repository_ids:[..]}; repo_fav=FavoriteInput {favorite}; "
        "delete_images=DeleteImages {image_ids:[..]}; create_tag=CreateTag "
        "{image_id, name, color}; update_tag=UpdateTag {color}; "
        "delete_tags=DeleteTags {tag_ids:[..]}.",
    ),
]

DOMAIN = DomainTool(
    domain="docker_registry",
    name="mlspace_docker_registry",
    title="MLSpace Docker Registry",
    summary="Manage the MLSpace Docker registry: repositories, images, tags, "
            "favorites, and registry credentials.",
    actions=ACTIONS,
    params=PARAMS,
    redact_result_keys=("password",),
)
