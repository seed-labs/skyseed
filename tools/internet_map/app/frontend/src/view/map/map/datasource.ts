import {DataSource as BaseDataSource} from '@/utils/map-datasource.ts';

export class DataSource extends BaseDataSource {
    // The live graph starts empty. Its first Runner snapshot supplies the
    // Scene vertices and links, including deployments without SEED ASN labels.
    get vertices() { return []; }
    get edges() { return []; }
    get groups() { return new Set<string>(); }
}
