# Media sources, licensing, and provenance

**Checkout limitation:** This inventory records provider behavior from the earlier source review. The `incubrix/` implementation package is absent from the current checkout, so the adapter behavior described below cannot currently be revalidated from source or tests. Do not treat this inventory alone as proof of commercial reuse rights.

This inventory describes the provider adapters and metadata checks present in the code. It is not legal advice, a rights clearance, or proof that every item is commercially reusable. Inspect the license attached to the exact item and follow its terms before publication.

## Still-image sources

| Provider | API/source used by the code | License and reuse handling | Attribution/provenance and limitations |
|---|---|---|---|
| Pixabay | [Images API](https://pixabay.com/api/) ([API documentation](https://pixabay.com/api/docs/)) | Records the Pixabay Content License and its general terms link. Provider API metadata is treated as rights-verified by the adapter; the app does not independently clear depicted people, property, or other third-party rights. | Stores item ID, page URL, contributor, tags, dimensions, and an attribution string. Review the exact source page and current terms. |
| Wikimedia Commons | [MediaWiki API](https://commons.wikimedia.org/w/api.php) ([API search documentation](https://www.mediawiki.org/wiki/API:Search)) | Reads item `LicenseShortName` and `LicenseUrl`; the adapter allows recognized CC0/public-domain/CC BY/CC BY-SA names and rejects ND and, when commercial use is configured, NC names. The retrieval pipeline also requires a source URL, image URL, `rights_verified`, and a license URL. Returned metadata is not independently checked for accuracy. | Stores the Commons description page, creator, license, attribution metadata, and share-alike metadata. Respect the item’s attribution/share-alike conditions. |
| Openverse | [Images API](https://api.openverse.org/v1/images/) | Enabled only when client ID and secret are configured. Requests CC0, public-domain, CC BY, and CC BY-SA candidates, validates the item license URL, and rejects attribution-required candidates with no creator/attribution. Openverse is an index, not a blanket license. | Stores the external landing page, creator/attribution, item license ID/version/URL, and source metadata. Follow the original provider’s terms. |
| Unsplash | [Photos API](https://api.unsplash.com/search/photos) ([API documentation](https://unsplash.com/documentation)) | Requires `UNSPLASH_ACCESS_KEY`, uses the [Unsplash License](https://unsplash.com/license), and calls the API download-trigger endpoint before downloading. | Stores the photo page, photographer, attribution, and download metadata. The response does not prove rights to depicted people/property or all third-party elements. |
| Pexels | [Photos API](https://api.pexels.com/v1/search) ([API documentation](https://www.pexels.com/api/documentation/)) | Requires `PEXELS_API_KEY` and records the [Pexels License](https://www.pexels.com/license/). Provider-level terms are not an independent rights audit of an individual image. | Stores the photo page, photographer, and attribution metadata. Check restrictions and third-party rights. |
| NASA Image and Video Library | [Images API](https://images-api.nasa.gov) ([API documentation](https://images.nasa.gov/docs/images.nasa.gov_api_docs.pdf)) | The adapter records NASA media guidance and item metadata but sets `rights_verified` to false. The current image retrieval pipeline rejects candidates without `rights_verified`, so NASA items should not be treated as eligible selected images under the current gate. `IMAGE_ALLOW_UNVERIFIED_NASA` does not make that candidate rights-verified. | Records NASA ID, source page, creator/center, and any copyright notice. NASA media can have item-specific restrictions. A stored older output contains NASA provenance and must be checked item-by-item before reuse. |

Default image-provider order in the current configuration is `pixabay,openverse,wikimedia,unsplash,nasa,pexels`. Providers requiring credentials are only added when their relevant credentials are configured. Provider errors are retained and do not prevent later configured image providers from being tried.

## Video sources and local footage

| Provider | API/source used by the code | License and reuse handling | Attribution/provenance and limitations |
|---|---|---|---|
| Pixabay | [Videos API](https://pixabay.com/api/videos/) ([API documentation](https://pixabay.com/api/docs/)) | The adapter records the [Pixabay Content License summary](https://pixabay.com/service/license-summary/) and marks provider metadata as rights-verified. It does not independently verify item-specific commercial rights or rights in depicted subjects. | Records video ID, source page, contributor, selected rendition/dimensions, and license metadata. |
| Pexels | [Videos API](https://api.pexels.com/videos/search) ([API documentation](https://www.pexels.com/api/documentation/)) | Requires `PEXELS_API_KEY`; records the [Pexels License](https://www.pexels.com/license/). Provider license metadata is not an independent audit of the downloaded file or depicted subjects. | Records video source URL, creator, chosen rendition, and license metadata. |
| Wikimedia Commons | [MediaWiki API](https://commons.wikimedia.org/w/api.php) | Rejects video candidates without accepted license metadata according to the Commons adapter and configured commercial-use policy. Individual item terms still apply. | Records description page, creator, license, and rights metadata. |
| User-provided `local_footage` | Files in the configured local footage directory | Considered only after the user confirms they have reuse rights. This is user attestation, not a license check. | Source is local/user supplied; third-party rights and watermarks are not independently checked. |

## AI-generated media and provenance flags

The current application does not call an image- or video-generation model. Groq may assist with scene analysis, while downloaded visuals come from media providers and uncovered scenes use procedurally drawn fallback graphics. A verified implementation defect caused some historical `ai_generated` flags to reflect AI-assisted analysis instead of generated media. The current code now records media `ai_generated: false` and stores `scene_analysis_method` separately. Do not use older `ai_generated: true` timeline entries as evidence that a scene's media was AI-generated.

## What the app does not verify

- Watermarks, visible logos/brands, faces, named people, sensitive-news suitability, or visual semantic relevance by computer vision.
- A provider’s item-level rights representations, commercial use rights for every jurisdiction/use, model/property releases, or rights in third-party elements.
- That creator attribution or share-alike requirements have been satisfied in a final publication.

The selected source URL, provider, license name/URL, attribution, retrieval status, and heuristic relevance score are kept in `timeline.json` and/or `manifest.json` when provided by the adapter. Fallback visuals are not sourced media. Inspect each item’s source page and current terms before making a rights claim. See the [assessment report](REPORT.md) and [per-requirement evidence checklist](docs/Assessment_Checklist.md).
