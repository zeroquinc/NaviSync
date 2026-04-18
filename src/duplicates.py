"""
Duplicate track and album-assignment logic.

All functions that prompt the user to resolve duplicate Navidrome tracks,
distribute scrobbles across album versions, and cache those decisions live here.
"""

# Sentinel returned by album-assignment prompts to signal "use album-aware divide"
_DIVIDE = "DIVIDE"


def analyze_album_distribution(scrobble_info, duplicates):
    """
    Analyze which albums the Last.fm scrobbles belong to and suggest distribution.

    Args:
        scrobble_info: Dict with 'timestamps' and album data from Last.fm
        duplicates: List of Navidrome track versions with 'album' field

    Returns:
        Dict with album distribution data: {navidrome_album: {'suggested_count': int, 'original_count': int}}
    """
    distribution = {
        dup['album'] if dup['album'] else "(No Album)": {
            'suggested_count': len(scrobble_info['timestamps']),
            'original_count': len(scrobble_info['timestamps'])
        }
        for dup in duplicates
    }
    return distribution


def recompute_manual_distribution(duplicates, cached_distribution, current_album_counts):
    """
    Re-compute a manual (select+distribution) assignment using fresh Last.fm album counts.

    The user previously chose where unmatched scrobbles should go.  We identify
    that "selected" track from the cached distribution (it's the one that received
    scrobbles beyond what pure album-name matching would assign), then re-apply the
    same routing with current counts.

    Args:
        duplicates: List of Navidrome track dicts with 'id' and 'album'.
        cached_distribution: The frozen {track_id: count} dict from the cache.
        current_album_counts: Fresh {album: count} dict from the scrobble cache.

    Returns:
        Fresh {track_id: count} dict, or None if we can't determine the routing.
    """
    if not current_album_counts:
        return None

    album_counts_norm = {
        (a or '').strip().lower(): count
        for a, count in current_album_counts.items()
    }

    matched_counts = {}
    for dup in duplicates:
        nav_norm = (dup['album'] or '').strip().lower()
        matched_counts[dup['id']] = album_counts_norm.get(nav_norm, 0)

    total_matched = sum(matched_counts.values())
    total_all = sum(current_album_counts.values())
    total_unmatched = total_all - total_matched

    selected_id = None
    if total_unmatched > 0:
        for dup in duplicates:
            tid = dup['id']
            cached_val = cached_distribution.get(tid, 0)
            if cached_val > matched_counts.get(tid, 0):
                selected_id = tid
                break

    if selected_id is None:
        selected_id = max(cached_distribution, key=lambda k: cached_distribution.get(k, 0), default=None)

    distribution = {}
    for dup in duplicates:
        count = matched_counts[dup['id']]
        if dup['id'] == selected_id:
            count += total_unmatched
        distribution[dup['id']] = count

    return distribution


def calculate_album_divide(duplicates, scrobble_info, album_counts=None):
    """
    Calculate album-aware divide distribution WITHOUT interactive output.

    Used internally when applying cached selections or getting the distribution.

    Args:
        duplicates: List of Navidrome track versions with 'id', 'album' fields
        scrobble_info: Dict with 'timestamps' and 'album_orig' from Last.fm

    Returns:
        Dict mapping track_id to calculated playcount
    """
    def distribute_equally(total):
        per_version = total // len(duplicates)
        remainder = total % len(duplicates)
        distribution = {}
        for idx, dup in enumerate(duplicates):
            count = per_version + (1 if idx < remainder else 0)
            distribution[dup['id']] = count
        return distribution

    if album_counts is not None:
        total_scrobbles = sum(album_counts.values())
        if total_scrobbles <= 0:
            return distribute_equally(len(scrobble_info['timestamps']))

        album_counts_norm = {
            (album or '').strip().lower(): count
            for album, count in album_counts.items()
        }

        distribution = {}
        matched_total = 0
        unmatched_ids = []

        for dup in duplicates:
            nav_album_norm = (dup['album'] or '').strip().lower()
            count = album_counts_norm.get(nav_album_norm)
            if count is None:
                distribution[dup['id']] = 0
                unmatched_ids.append(dup['id'])
            else:
                distribution[dup['id']] = count
                matched_total += count

        remainder = total_scrobbles - matched_total
        if remainder > 0:
            target_ids = unmatched_ids if unmatched_ids else [dup['id'] for dup in duplicates]
            per_version = remainder // len(target_ids)
            extra = remainder % len(target_ids)
            for idx, tid in enumerate(target_ids):
                distribution[tid] += per_version + (1 if idx < extra else 0)

        if matched_total > 0:
            return distribution

        return distribute_equally(total_scrobbles)

    # Legacy behavior when no album counts are available
    total_scrobbles = len(scrobble_info['timestamps'])
    lastfm_album = scrobble_info.get('album_orig', '').strip()

    if not lastfm_album:
        return distribute_equally(total_scrobbles)

    exact_match = None
    for dup in duplicates:
        nav_album = (dup['album'] or '').strip()
        if nav_album.lower() == lastfm_album.lower():
            exact_match = dup
            break

    if exact_match:
        distribution = {}
        for dup in duplicates:
            distribution[dup['id']] = total_scrobbles if dup['id'] == exact_match['id'] else 0
        return distribution

    return distribute_equally(total_scrobbles)


def detect_album_mismatch(duplicates, album_counts):
    """
    Detect which Last.fm albums match Navidrome albums and which don't.

    Args:
        duplicates: List of Navidrome track versions with 'album' field
        album_counts: Dict of {album: count} from Last.fm cache

    Returns:
        Tuple of (has_unmatched, matched_list, unmatched_list)
        - matched_list: List of (navidrome_album, lastfm_album, count) that match
        - unmatched_list: List of (lastfm_album, count) that don't match any Navidrome album
    """
    navidrome_albums_norm = {(dup['album'] or '').strip().lower(): dup['album'] for dup in duplicates}

    matched_list = []
    unmatched_list = []

    for lastfm_album, count in album_counts.items():
        lastfm_album_norm = (lastfm_album or '').strip().lower()

        if lastfm_album_norm in navidrome_albums_norm:
            nav_album = navidrome_albums_norm[lastfm_album_norm]
            matched_list.append((nav_album, lastfm_album, count))
        else:
            unmatched_list.append((lastfm_album, count))

    has_unmatched = len(unmatched_list) > 0
    return has_unmatched, matched_list, unmatched_list


def prompt_user_for_album_assignment(duplicates, album_counts, matched_albums, unmatched_albums, lastfm_artist, lastfm_track):
    """
    When some Last.fm albums match but others don't, prompt user where to assign unmatched scrobbles.

    Args:
        duplicates: List of Navidrome track versions with 'id', 'album' fields
        album_counts: Dict of {album: count} from Last.fm cache
        matched_albums: List of (navidrome_album, lastfm_album, count) that matched
        unmatched_albums: List of (lastfm_album, count) that didn't match
        lastfm_artist: Last.fm artist name (for display)
        lastfm_track: Last.fm track name (for display)

    Returns:
        Tuple of (selected_track_ids, distribution) or (None, None) to skip.
        selected_track_ids may be the string "DIVIDE" to signal album-divide fallback.
    """
    total_matched = sum(count for _, _, count in matched_albums)
    total_unmatched = sum(count for _, count in unmatched_albums)

    print(f"\n⚠️  Partial album match for: {lastfm_artist} - {lastfm_track}")
    print(f"\n   ✓ Matched Last.fm → Navidrome:")
    for nav_album, lastfm_album, count in matched_albums:
        nav_display = nav_album if nav_album else "(No Album)"
        lastfm_display = lastfm_album if lastfm_album else "(No Album)"
        print(f"      {lastfm_display} ({count}) → {nav_display}")

    print(f"\n   ✗ Unmatched Last.fm albums (need assignment):")
    for lastfm_album, count in unmatched_albums:
        lastfm_display = lastfm_album if lastfm_album else "(No Album)"
        print(f"      {lastfm_display}: {count} scrobbles")

    print(f"\n   Where should the {total_unmatched} unmatched scrobbles go?")

    for idx, dup in enumerate(duplicates, 1):
        album_info = dup['album'] if dup['album'] else "(No Album)"
        print(f"      [{idx}] {album_info}")

    print(f"   [D] Album-aware divide (try to split remaining by album info)")
    print(f"   [0] Skip this track")

    while True:
        choice = input(f"\n   → Assign unmatched scrobbles to [1-{len(duplicates)}/D/0]: ").strip().upper()

        if choice == '0':
            print(f"   ⏭️  Skipped")
            return None, None

        if choice == 'D':
            print(f"   ✅ Using album-aware divide for unmatched scrobbles")
            return _DIVIDE, None

        try:
            idx = int(choice)
            if 1 <= idx <= len(duplicates):
                selected = duplicates[idx - 1]
                album_name = selected['album'] if selected['album'] else "(No Album)"

                distribution = {}
                for dup in duplicates:
                    dup_album_norm = (dup['album'] or '').strip().lower()

                    matched_count = 0
                    for nav_album, lastfm_album, count in matched_albums:
                        nav_norm = (nav_album or '').strip().lower()
                        if nav_norm == dup_album_norm:
                            matched_count = count
                            break

                    if dup['id'] == selected['id']:
                        distribution[dup['id']] = matched_count + total_unmatched
                    else:
                        distribution[dup['id']] = matched_count

                print(f"\n   ✅ Matched scrobbles stay with their albums, unmatched ({total_unmatched}) go to: {album_name}")
                return [selected['id']], distribution
        except ValueError:
            pass

        print(f"   ⚠️  Invalid choice")


def prompt_user_for_album_assignment_full_mismatch(duplicates, album_counts, lastfm_artist, lastfm_track):
    """
    When NO Last.fm albums match any Navidrome albums, prompt user for option.

    Args:
        duplicates: List of Navidrome track versions with 'id', 'album' fields
        album_counts: Dict of {album: count} from Last.fm cache
        lastfm_artist: Last.fm artist name (for display)
        lastfm_track: Last.fm track name (for display)

    Returns:
        Tuple of (selected_track_ids, distribution) or (None, None) to skip.
        selected_track_ids may be the string "DIVIDE" to signal album-divide fallback.
    """
    print(f"\n⚠️  Album mismatch: Last.fm album(s) don't match your Navidrome library")
    print(f"   Track: {lastfm_artist} - {lastfm_track}")

    print(f"\n   Last.fm has scrobbles from:")
    for album, count in sorted(album_counts.items(), key=lambda x: x[1], reverse=True):
        album_name = album if album else "(No Album)"
        print(f"      • {album_name}: {count} scrobbles")

    print(f"\n   Your Navidrome library has these versions:")
    for idx, dup in enumerate(duplicates, 1):
        album_info = dup['album'] if dup['album'] else "(No Album)"
        print(f"      [{idx}] {album_info}")

    print(f"\n   [D] Album-aware divide (split by Last.fm album info)")
    print(f"   [S] Single version (choose which gets all scrobbles):")
    for idx, dup in enumerate(duplicates, 1):
        album_info = dup['album'] if dup['album'] else "(No Album)"
        print(f"       [{idx}] {album_info}")
    print(f"   [0] Skip this track")

    while True:
        total_scrobbles = sum(album_counts.values())
        choice = input(f"\n   → How should these {total_scrobbles} scrobbles be assigned? [D/S/0]: ").strip().upper()

        if choice == '0':
            print(f"   ⏭️  Skipped")
            return None, None

        if choice == 'D':
            print(f"   ✅ Using album-aware divide")
            return _DIVIDE, None

        if choice == 'S':
            print(f"\n   Select which version should receive all scrobbles:")
            while True:
                try:
                    idx_choice = input(f"   → Enter version [1-{len(duplicates)}]: ").strip()
                    idx = int(idx_choice)
                    if 1 <= idx <= len(duplicates):
                        selected = duplicates[idx - 1]
                        album_name = selected['album'] if selected['album'] else "(No Album)"
                        print(f"\n   ✅ All scrobbles will go to: {album_name}")

                        distribution = {}
                        total_scrobbles = sum(album_counts.values())
                        for dup in duplicates:
                            distribution[dup['id']] = total_scrobbles if dup['id'] == selected['id'] else 0
                        return [selected['id']], distribution
                except ValueError:
                    pass
                print(f"   ⚠️  Invalid choice")
        else:
            print(f"   ⚠️  Invalid choice. Please enter D, S, or 0")


def process_album_divide(duplicates, scrobble_info, cache, lastfm_artist, lastfm_track):
    """
    Process album-aware divide: distribute scrobbles across album versions based on Last.fm data.

    Args:
        duplicates: List of Navidrome track versions with 'id', 'album' fields
        scrobble_info: Dict with 'timestamps' and 'album_orig' from Last.fm
        cache: ScrobbleCache instance (for fetching album scrobble counts)
        lastfm_artist: Last.fm artist name
        lastfm_track: Last.fm track name

    Returns:
        Dict mapping track_id to calculated playcount
    """
    album_counts = cache.get_album_scrobble_counts(lastfm_artist, lastfm_track)
    total_scrobbles = sum(album_counts.values())

    print(f"\n📀 Album-aware divide for: {lastfm_artist} - {lastfm_track}")
    print(f"   Total scrobbles: {total_scrobbles}")
    print(f"   Navidrome versions:")

    for idx, dup in enumerate(duplicates, 1):
        album_name = dup['album'] if dup['album'] else "(No Album)"
        print(f"      [{idx}] {album_name}")

    if total_scrobbles <= 0:
        print(f"   ⚠️  Last.fm scrobbles don't have album information")
        print(f"   → Dividing {len(scrobble_info['timestamps'])} scrobbles equally among {len(duplicates)} versions")
        distribution = calculate_album_divide(duplicates, scrobble_info)
    else:
        print(f"   Last.fm album counts:")
        for album, count in album_counts.items():
            album_name = album if album else "(No Album)"
            print(f"      {album_name}: {count} scrobbles")

        distribution = calculate_album_divide(duplicates, scrobble_info, album_counts=album_counts)

    for dup in duplicates:
        album_name = dup['album'] if dup['album'] else "(No Album)"
        print(f"      {album_name}: {distribution[dup['id']]} scrobbles")

    return distribution


def prompt_user_for_duplicate_selection(duplicates, scrobble_info=None, album_maps=None):
    """
    Prompt user to select which album version(s) should receive the play count.

    Args:
        duplicates: List of dicts with Navidrome track info including 'id', 'album', 'artist', 'title'
        scrobble_info: Optional dict with Last.fm scrobble info including 'timestamps'
        album_maps: Optional dict mapping navidrome albums to last.fm albums for album-aware divide

    Returns:
        Tuple of (selected_track_ids, distribution_dict) where distribution_dict is None unless
        album-divide is used. selected_track_ids is None if user wants to skip all.
        When album-divide is selected, returns (duplicates_list, scrobble_info) as a special marker.
    """
    print(f"\n⚠️  Multiple versions of the same track found in Navidrome:")
    print(f"   Track: {duplicates[0]['artist']} - {duplicates[0]['title']}")
    print(f"\n   Found in {len(duplicates)} different location(s):")

    def format_duration(seconds):
        if not seconds or seconds <= 0:
            return "--:--"
        minutes = int(seconds // 60)
        secs = int(seconds % 60)
        return f"{minutes}:{secs:02d}"

    for idx, dup in enumerate(duplicates, 1):
        album_info = dup['album'] if dup['album'] else "(No Album)"

        info_parts = []
        if dup.get('track_number'):
            track_str = f"Track {dup['track_number']}"
            if dup.get('disc_number') and dup['disc_number'] > 1:
                track_str = f"Disc {dup['disc_number']}, {track_str}"
            info_parts.append(track_str)

        if dup.get('duration'):
            info_parts.append(f"({format_duration(dup['duration'])})")

        additional_info = f" - {' '.join(info_parts)}" if info_parts else ""
        print(f"   [{idx}] {album_info}{additional_info}")

    print(f"   [A] Apply to ALL versions")
    if scrobble_info and len(duplicates) > 1:
        total_scrobbles = len(scrobble_info.get('timestamps', []))
        print(f"   [B] Album-aware divide (divide {total_scrobbles} scrobbles by album)")
    print(f"   [0] Skip all versions")

    while True:
        options = f"1-{len(duplicates)}/A"
        if scrobble_info and len(duplicates) > 1:
            options += "/B"
        options += "/0"
        choice = input(f"\n   → Select which version(s) to update [{options}]: ").strip().upper()

        if choice == '0':
            print(f"   ⏭️  Skipped all versions")
            return None, None

        if choice == 'A':
            print(f"   ✅ Will update ALL versions")
            return [dup['id'] for dup in duplicates], None

        if choice == 'B' and scrobble_info and len(duplicates) > 1:
            print(f"   📀 Album-aware divide selected")
            return duplicates, scrobble_info  # special marker for album-divide

        try:
            idx = int(choice)
            if 1 <= idx <= len(duplicates):
                selected = duplicates[idx - 1]
                album_name = selected['album'] if selected['album'] else "(No Album)"
                print(f"   ✅ Selected: {album_name}")
                return [selected['id']], None
        except ValueError:
            pass

        print(f"   ⚠️  Invalid choice. Please enter a number between {options}")


def prompt_user_for_loved_selection(duplicates, starred_ids):
    """
    Prompt user to select which duplicate version(s) should be starred.

    Args:
        duplicates: List of dicts with Navidrome track info
        starred_ids: Set of duplicate IDs already starred in Navidrome

    Returns:
        List of selected track IDs, or empty list to skip starring
    """
    print(f"\n⭐ Loved track has multiple versions in Navidrome:")
    print(f"   Track: {duplicates[0]['artist']} - {duplicates[0]['title']}")
    print(f"\n   Found in {len(duplicates)} different location(s):")

    for idx, dup in enumerate(duplicates, 1):
        album_info = dup['album'] if dup['album'] else "(No Album)"
        star_marker = " ★" if dup['id'] in starred_ids else ""
        print(f"   [{idx}] {album_info}{star_marker}")

    print(f"   [A] Apply to ALL versions")
    print(f"   [0] Skip starring")

    while True:
        choice = input(f"\n   → Select which version(s) to star [1-{len(duplicates)}/A/0]: ").strip().upper()

        if choice == '0':
            print(f"   ⏭️  Skipped starring")
            return []

        if choice == 'A':
            print(f"   ✅ Will star ALL versions")
            return [dup['id'] for dup in duplicates]

        try:
            idx = int(choice)
            if 1 <= idx <= len(duplicates):
                selected = duplicates[idx - 1]
                album_name = selected['album'] if selected['album'] else "(No Album)"
                print(f"   ✅ Selected: {album_name}")
                return [selected['id']]
        except ValueError:
            pass

        print(f"   ⚠️  Invalid choice. Please enter a number between 1-{len(duplicates)} or A/0")


def resolve_album_divide_selection(duplicates, scrobble_info, cache, lastfm_artist, lastfm_track):
    """
    Prompt user for a duplicate track selection and handle all album-divide logic.

    Encapsulates the prompt → album-mismatch check → cache-save flow so it can
    be called from multiple code paths without duplication.

    Args:
        duplicates: List of Navidrome track dicts with 'id', 'album', etc.
        scrobble_info: Aggregated Last.fm scrobble dict for this track.
        cache: ScrobbleCache instance.
        lastfm_artist: Last.fm artist name.
        lastfm_track: Last.fm track name.

    Returns:
        (selected_ids, album_divide_result, skip)
        - selected_ids: list of Navidrome track IDs to update, or None
        - album_divide_result: {track_id: count} dict when album-divide was used, else None
        - skip: True when the caller should skip to the next track (user cancelled)
    """
    result, divide_info = prompt_user_for_duplicate_selection(duplicates, scrobble_info)

    # Album-aware divide: result is the duplicates list, divide_info is scrobble_info
    if (divide_info is not None
            and isinstance(result, list)
            and result
            and isinstance(result[0], dict)
            and 'id' in result[0]):

        album_counts = cache.get_album_scrobble_counts(lastfm_artist, lastfm_track)
        if album_counts:
            has_unmatched, matched_albums, unmatched_albums = detect_album_mismatch(duplicates, album_counts)
            if has_unmatched:
                if matched_albums:
                    assignment_choice, manual_distribution = prompt_user_for_album_assignment(
                        duplicates, album_counts, matched_albums, unmatched_albums,
                        lastfm_artist, lastfm_track
                    )
                else:
                    assignment_choice, manual_distribution = prompt_user_for_album_assignment_full_mismatch(
                        duplicates, album_counts, lastfm_artist, lastfm_track
                    )

                if assignment_choice is None:
                    return None, None, True  # user cancelled → skip track

                if assignment_choice != _DIVIDE:
                    # User chose a specific manual assignment
                    all_duplicate_ids = [dup['id'] for dup in duplicates]
                    cache.save_duplicate_selection(
                        lastfm_artist, lastfm_track,
                        all_duplicate_ids, mode="select", distribution=manual_distribution
                    )
                    return assignment_choice, manual_distribution, False

        album_divide_result = process_album_divide(result, scrobble_info, cache, lastfm_artist, lastfm_track)
        selected_ids = list(album_divide_result.keys())
        cache.save_duplicate_selection(
            lastfm_artist, lastfm_track, selected_ids,
            mode="divide", distribution=album_divide_result
        )
        return selected_ids, album_divide_result, False

    if result:
        cache.save_duplicate_selection(lastfm_artist, lastfm_track, result, mode="select")
        return result, None, False

    return None, None, False  # user chose to skip
