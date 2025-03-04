async function getJSON(url) {
    const resp = await fetch(url);
    if (!resp.ok) {
        alert('Could not load tree data. See console for more details.');
        console.error(await resp.text());
        return [];
    }
    return resp.json();
}

function createTreeNode(id, text, icon, children = false) {
    return { id, text, children, itree: { icon } };
}

async function getHubs() {
    const hubs = await getJSON('/api/hubs');
    return hubs.map(hub => createTreeNode(`hub|${hub.id}`, hub.attributes.name, 'icon-hub', true));
}

async function getProjects(hubId) {
    const projects = await getJSON(`/api/hubs/${hubId}/projects`);
    return projects.map(project => createTreeNode(`project|${hubId}|${project.id}`, project.attributes.name, 'icon-project', true));
}

async function getContents(hubId, projectId, folderId = null) {
    const contents = await getJSON(`/api/hubs/${hubId}/projects/${projectId}/contents` + (folderId ? `?folder_id=${folderId}` : ''));
    // Use Promise.all to handle asynchronous operations concurrently
    const treeNodes = await Promise.all(contents.map(async item => {
        if (item.type === 'folders') {
            return createTreeNode(`folder|${hubId}|${projectId}|${item.id}`, item.attributes.displayName, 'icon-my-folder', true);
        } else {
            const versions = await getJSON(`/api/hubs/${hubId}/projects/${projectId}/contents/${item.id}/versions`);
            return createTreeNode(`version|${versions[0].id}`, item.attributes.displayName, 'icon-item');
        }
    }));

    return treeNodes;
}

async function getVersions(hubId, projectId, itemId) {
    const versions = await getJSON(`/api/hubs/${hubId}/projects/${projectId}/contents/${itemId}/versions`);
    return [];
    //return versions.map(version => createTreeNode(`version|${version.id}`, version.attributes.createTime, 'icon-version'));
}

export function initTree(selector, onSelectionChanged) {
    const tree = new InspireTree({
        data: function (node) {
            if (!node || !node.id) {
                return getHubs();
            } else {
                const tokens = node.id.split('|');
                switch (tokens[0]) {
                    case 'hub': return getProjects(tokens[1]);
                    case 'project': return getContents(tokens[1], tokens[2]);
                    case 'folder': return getContents(tokens[1], tokens[2], tokens[3]);
                    default: return [];
                }
            }
        }
    });

    tree.on('node.click', function (event, node) {
        event.preventTreeDefault();
        
        const tokens = node.id.split('|');
        
        if (tokens[0] === 'version') {
            console.log(node)
            const versionId = tokens[1];
            let projectId = null;
            if (node.itree.parent && node.itree.parent.id) {
                
                projectId = node.itree.parent.id.split('|')[2];  // Extract project ID from the parent node's ID
            }

            if (!projectId) {
                console.error('Project ID could not be determined');
                return;
            }
            console.log('Project ID:', projectId);
            console.log('Version ID:', versionId);
            onSelectionChanged(projectId, versionId);  // Pass both project ID and version ID
        }
    });

    return new InspireTreeDOM(tree, { target: selector });
}
